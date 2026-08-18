#!/bin/sh
# E-22 단계 3(회사 역할)·단계 4(사건 구조·중복 제거)를 운영 DB에 적재한다.
#
#   bash infra/operations/vm_load_company_ontology.sh
#
# 반드시 배포(deploy_production.sh) 뒤에 실행한다 — 적재 코드와 운영 DB의
# 인포스탁 파서 버전이 같아야 raw_text 대조가 어긋나지 않는다.
# VM에 이미 올라가 있는 인포스탁 번들만 읽는다. 외부 API를 부르지 않는다.
# 두 워커 모두 revision append라 여러 번 실행해도 행이 늘지 않는다.
set -eu
VM="${1:-ubuntu@api.dayjaview.duckdns.org}"
C="sudo docker compose -f /opt/dayjaview/repo/infra/deployment/compose.production.yml"
ART="/opt/dayjaview/data/ontology"

# 과거 사명 색인은 deploy 5-3이 옮겨 둔다. 없으면 색인 없이 진행한다.
KRX_FLAG=$(ssh "$VM" "test -f $ART/krx_name_windows.json \
    && echo '--krx-names /workspace/research/ontology/krx_name_windows.json' || true")
[ -n "$KRX_FLAG" ] || echo "주의: krx_name_windows.json 없음 — 과거 사명 해석 없이 적재한다" >&2

run_stage() {
    ssh "$VM" "$C run --rm --no-deps -T \
        -v $ART:/workspace/research/ontology \
        infostock-bootstrap python $1 \
        --input-dir /workspace/data/infostock-import \
        --output-dir /workspace/research/ontology \
        $KRX_FLAG \
        --load --database-url-env INFOSTOCK_DATABASE_URL"
}

echo "== 단계 3: history 회사 역할 적재"
run_stage apps/worker-batch/ontology/label_company_events.py

echo "== 단계 4: 고유 사건·프로젝트·금액 fact 적재"
run_stage apps/worker-batch/ontology/build_company_events.py

echo "완료. 확인: coverage 보고서는 VM의 $ART 아래에 남는다."
