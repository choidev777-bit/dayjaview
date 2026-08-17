"""실 구글 로그인과 Postgres identity 조립(F-21).

env에 Google OAuth client 키가 있으면 fixture provider 대신 실제 provider를,
`DATABASE_URL`이 있으면 in-memory 대신 Postgres identity 저장소를 쓴다. 둘 다
없으면 지금까지의 fixture 조립 그대로라 키 없이 로컬에서 띄우던 경로가 깨지지
않는다. 키를 반쪽만 설정한 경우는 조용히 fixture로 내려가지 않고 즉시 실패한다
— fixture provider는 등록된 데모 code를 그대로 받아들이므로 실배포에서 fixture로
떨어지는 것이 곧 인증 우회다.

secret 값은 이 모듈 밖으로 나가지 않는다. 조립 결과는 어느 provider·저장소를
골랐는지만 모드 문자열로 알린다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from packages.identity import (
    FixtureGoogleOAuthProvider,
    GoogleOAuthProvider,
    HttpGoogleOAuthProvider,
    IdentityRepository,
    IdentityService,
    InMemoryIdentityRepository,
    InMemoryTargetCatalog,
    PostgresIdentityRepository,
    RuntimeOperatorStatus,
    SystemClock,
    TargetCatalog,
)
from packages.identity.postgres import DbConnection
from packages.identity.security import Clock
from packages.ontology.outcomes import SqliteOutcomeReader
from packages.ontology.query_answers import OUTCOME_RANGE_FROM, QueryAvailability
from packages.ontology.query_contracts import QueryType
from packages.ontology.research_postgres import (
    PostgresResearchRepository,
    load_question_catalog,
)
from packages.operator import (
    InMemoryOperatorRepository,
    OperatorRepository,
    PostgresOperatorRepository,
)

from .app import IdentityApiApp, create_app
from .config import ApiSettings
from .operator_boundary import StaticOperatorStatusSource
from .product import EmptyProductReadRepository, ProductReadRepository
from .realtime import RealtimeSnapshotHub
from .research import ResearchBoundary

GOOGLE_CLIENT_ID_ENV = "GOOGLE_OAUTH_CLIENT_ID"
GOOGLE_CLIENT_SECRET_ENV = "GOOGLE_OAUTH_CLIENT_SECRET"
IDENTITY_DATABASE_DSN_ENV = "DATABASE_URL"
# 특징테마는 인포스탁 적재 DB에 있다. 워커 env는 이 이름만 준다.
INFOSTOCK_DATABASE_DSN_ENV = "INFOSTOCK_DATABASE_URL"
CURSOR_SIGNING_SECRET_ENV = "SESSION_SIGNING_SECRET"
# 사람 검수를 통과해 공개할 질의 유형(쉼표 구분). 비면 전부 품질 미검증이다.
RESEARCH_VERIFIED_QUERY_TYPES_ENV = "RESEARCH_VERIFIED_QUERY_TYPES"
# E-16 일봉 corpus 경로. 없으면 결과 질문 gate가 닫힌다.
PRICE_CORPUS_PATH_ENV = "PRICE_CORPUS_PATH"
DEPLOYMENT_VERSION_ENV = "DAYJAVIEW_DEPLOYMENT_VERSION"
DEPLOYMENT_COMMIT_ENV = "DAYJAVIEW_COMMIT"

GOOGLE_MODE = "GOOGLE"
FIXTURE_MODE = "FIXTURE"
POSTGRES_STORE = "POSTGRES"
MEMORY_STORE = "MEMORY"

DbConnect = Callable[[str], DbConnection]


@dataclass(frozen=True, slots=True)
class ProductionIdentityEnvironment:
    """실행 중인 조립 결과. secret이 아니라 고른 모드만 담는다."""

    app: IdentityApiApp
    service: IdentityService
    realtime_hub: RealtimeSnapshotHub
    operator_repository: OperatorRepository
    google_mode: str
    identity_store: str
    fixture_oauth_provider: FixtureGoogleOAuthProvider | None
    _closers: tuple[Callable[[], None], ...]

    @property
    def uses_real_google(self) -> bool:
        return self.google_mode == GOOGLE_MODE

    def close(self) -> None:
        for closer in self._closers:
            closer()


def create_production_app(
    environment: Mapping[str, str],
    *,
    settings: ApiSettings,
    product_repository: ProductReadRepository | None = None,
    target_catalog: TargetCatalog | None = None,
    realtime_hub: RealtimeSnapshotHub | None = None,
    operator_repository: OperatorRepository | None = None,
    operator_status: RuntimeOperatorStatus | None = None,
    clock: Clock | None = None,
    connect: DbConnect | None = None,
) -> ProductionIdentityEnvironment:
    """env가 있으면 실 provider·Postgres로, 없으면 fixture로 앱을 조립한다."""

    effective_clock = clock or SystemClock()
    policy = settings.identity_policy()
    closers: list[Callable[[], None]] = []

    provider, google_mode, fixture_provider = _oauth_provider(
        environment,
        redirect_uri=policy.oauth_redirect_uri,
        closers=closers,
    )
    repository, identity_store = _identity_repository(
        environment,
        connect=connect,
        closers=closers,
    )
    service = IdentityService(
        repository=repository,
        oauth_provider=provider,
        target_catalog=target_catalog or InMemoryTargetCatalog(()),
        policy=policy,
        clock=effective_clock,
        cursor_secret=_cursor_secret(environment, identity_store=identity_store),
    )
    effective_hub = realtime_hub or RealtimeSnapshotHub()
    effective_operator_repository = operator_repository or _operator_repository(
        environment,
        connect=connect,
        closers=closers,
    )
    research_service = _research_service(
        environment, connect=connect, closers=closers, clock=effective_clock
    )
    runtime_status = operator_status or RuntimeOperatorStatus(
        deployment_version=(
            environment.get(DEPLOYMENT_VERSION_ENV, "").strip() or "local"
        ),
        commit=environment.get(DEPLOYMENT_COMMIT_ENV, "").strip() or "0000000",
        started_at=effective_clock.now(),
        services=(),
    )
    app = create_app(
        identity_service=service,
        operator_status_source=StaticOperatorStatusSource(runtime_status),
        settings=settings,
        product_repository=product_repository or EmptyProductReadRepository(),
        research_service=research_service,
        realtime_hub=effective_hub,
        operator_repository=effective_operator_repository,
        clock=effective_clock,
    )
    return ProductionIdentityEnvironment(
        app,
        service,
        effective_hub,
        effective_operator_repository,
        google_mode,
        identity_store,
        fixture_provider,
        tuple(closers),
    )


def _cursor_secret(
    environment: Mapping[str, str],
    *,
    identity_store: str,
) -> bytes | None:
    """관심 목록 커서 서명 키.

    없으면 `IdentityService`가 프로세스마다 무작위로 만든다. 인스턴스가 하나면
    문제가 없지만, 같은 저장소를 보는 인스턴스가 둘 이상이면 한쪽이 발급한
    커서를 다른 쪽이 거부해 목록 2페이지부터 실패한다. 그래서 공유 저장소를
    쓰는 조립에서는 비어 있으면 조용히 넘어가지 않고 실패시킨다.
    """

    value = environment.get(CURSOR_SIGNING_SECRET_ENV, "").strip()
    if not value:
        if identity_store == POSTGRES_STORE:
            raise ValueError(
                f"{CURSOR_SIGNING_SECRET_ENV}가 비어 있습니다. "
                "공유 저장소를 쓰면 인스턴스마다 커서 서명 키가 달라져 목록 넘김이 깨집니다."
            )
        return None
    secret = value.encode("utf-8")
    if len(secret) < 32:
        raise ValueError(f"{CURSOR_SIGNING_SECRET_ENV}는 32바이트 이상이어야 합니다.")
    return secret


def _oauth_provider(
    environment: Mapping[str, str],
    *,
    redirect_uri: str,
    closers: list[Callable[[], None]],
) -> tuple[GoogleOAuthProvider, str, FixtureGoogleOAuthProvider | None]:
    client_id = environment.get(GOOGLE_CLIENT_ID_ENV, "").strip()
    client_secret = environment.get(GOOGLE_CLIENT_SECRET_ENV, "").strip()
    if bool(client_id) != bool(client_secret):
        missing = GOOGLE_CLIENT_ID_ENV if not client_id else GOOGLE_CLIENT_SECRET_ENV
        raise ValueError(
            f"{missing}가 비어 있습니다. Google OAuth는 client id와 secret이 모두 있어야 합니다."
        )
    if not client_id:
        fixture = FixtureGoogleOAuthProvider(expected_redirect_uri=redirect_uri)
        return fixture, FIXTURE_MODE, fixture
    provider = HttpGoogleOAuthProvider(
        client_id=client_id,
        client_secret=client_secret,
        expected_redirect_uri=redirect_uri,
    )
    closers.append(provider.close)
    return provider, GOOGLE_MODE, None


def _identity_repository(
    environment: Mapping[str, str],
    *,
    connect: DbConnect | None,
    closers: list[Callable[[], None]],
) -> tuple[IdentityRepository, str]:
    dsn = environment.get(IDENTITY_DATABASE_DSN_ENV, "").strip()
    if not dsn:
        return InMemoryIdentityRepository(), MEMORY_STORE
    if connect is None:
        import psycopg

        # psycopg cursor overload가 저장소의 DbConnection Protocol과 이름만 다르다.
        def connect_psycopg(target: str) -> DbConnection:
            connection: Any = psycopg.connect(target)
            return connection

        connect = connect_psycopg
    # 파이프라인 저장소와 연결을 나눠 쓰지 않는다. identity는 요청마다 쓰고
    # 파이프라인은 발행 루프에서 쓰므로 같은 connection을 공유하면 서로의
    # 트랜잭션 경계에 끼어든다.
    connection = connect(dsn)
    close = getattr(connection, "close", None)
    if callable(close):
        closers.append(close)
    return PostgresIdentityRepository(connection), POSTGRES_STORE


def _research_service(
    environment: Mapping[str, str],
    *,
    connect: DbConnect | None,
    closers: list[Callable[[], None]],
    clock: Clock,
) -> ResearchBoundary | None:
    """리서치 읽기 전용 연결. DSN이 없으면 기능을 열지 않는다.

    사람 검수를 통과한 질의 유형만 연다(계획서 11.1.2). 검수된 행이 없으면
    `RESEARCH_VERIFIED_QUERY_TYPES`가 비고 모든 유형이 `품질 미검증`으로 답한다.
    """

    dsn = environment.get(INFOSTOCK_DATABASE_DSN_ENV, "").strip() or environment.get(
        IDENTITY_DATABASE_DSN_ENV, ""
    ).strip()
    if not dsn:
        return None
    if connect is None:
        import psycopg

        def connect_psycopg(target: str) -> DbConnection:
            connection: Any = psycopg.connect(target)
            return connection

        connect = connect_psycopg
    connection = connect(dsn)
    close = getattr(connection, "close", None)
    if callable(close):
        closers.append(close)
    price_reader = _price_reader(environment, closers=closers)
    return ResearchBoundary(
        catalog=lambda: load_question_catalog(cast(Any, connection)),
        repository=PostgresResearchRepository(
            cast(Any, connection), price_reader=price_reader
        ),
        availability=QueryAvailability(
            human_verified=_verified_query_types(environment),
            outcome_gate_open=price_reader is not None,
            outcome_range_from=(
                OUTCOME_RANGE_FROM
                if price_reader is None
                else price_reader.price_range_from()
            ),
        ),
    )


def _verified_query_types(environment: Mapping[str, str]) -> frozenset[QueryType]:
    raw = environment.get(RESEARCH_VERIFIED_QUERY_TYPES_ENV, "").strip()
    if not raw:
        return frozenset()
    values: set[QueryType] = set()
    for item in raw.split(","):
        name = item.strip()
        if not name:
            continue
        values.add(QueryType(name))
    return frozenset(values)


def _price_reader(
    environment: Mapping[str, str],
    *,
    closers: list[Callable[[], None]],
) -> SqliteOutcomeReader | None:
    """E-16 가격 corpus. 없으면 결과 질문 gate를 닫아 둔다."""

    path = environment.get(PRICE_CORPUS_PATH_ENV, "").strip()
    if not path:
        return None
    reader = SqliteOutcomeReader(path)
    closers.append(reader.close)
    return reader


def _operator_repository(
    environment: Mapping[str, str],
    *,
    connect: DbConnect | None,
    closers: list[Callable[[], None]],
) -> OperatorRepository:
    dsn = environment.get(IDENTITY_DATABASE_DSN_ENV, "").strip()
    if not dsn:
        return InMemoryOperatorRepository()
    if connect is None:
        import psycopg

        def connect_psycopg(target: str) -> DbConnection:
            connection: Any = psycopg.connect(target)
            return connection

        connect = connect_psycopg
    connection = connect(dsn)
    close = getattr(connection, "close", None)
    if callable(close):
        closers.append(close)
    return PostgresOperatorRepository(connection)
