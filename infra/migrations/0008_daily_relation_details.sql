BEGIN;

-- Daily 게시물 재파싱으로 늘어난 값을 담는다. 섹션 상세 문단은 SECTION_DETAIL
-- 관계로, 등락률 표는 종목별 시세 컬럼으로 들어온다. 원문(raw_body)은 그대로
-- 두고 파생 값만 늘리므로 재수집이 필요하지 않다.

ALTER TABLE core.infostock_daily_relations
    DROP CONSTRAINT IF EXISTS infostock_daily_relations_relation_type_check;

ALTER TABLE core.infostock_daily_relations
    ADD CONSTRAINT ck_infostock_daily_relation_type CHECK (
        relation_type IN (
            'THEME', 'STOCK', 'THEME_STOCK', 'DESCRIPTION', 'SECTION_DETAIL'
        )
    );

ALTER TABLE core.infostock_daily_relations
    ADD COLUMN IF NOT EXISTS paragraph_no integer,
    ADD COLUMN IF NOT EXISTS theme_change_rate numeric(9, 2),
    ADD COLUMN IF NOT EXISTS close_price bigint,
    ADD COLUMN IF NOT EXISTS change_rate numeric(9, 2),
    ADD COLUMN IF NOT EXISTS trade_volume bigint,
    ADD COLUMN IF NOT EXISTS open_price bigint,
    ADD COLUMN IF NOT EXISTS high_price bigint,
    ADD COLUMN IF NOT EXISTS low_price bigint;

ALTER TABLE core.infostock_daily_relations
    ADD CONSTRAINT ck_infostock_daily_relation_paragraph
        CHECK (paragraph_no IS NULL OR paragraph_no >= 0),
    ADD CONSTRAINT ck_infostock_daily_relation_quote_sign CHECK (
        (close_price IS NULL OR close_price >= 0)
        AND (trade_volume IS NULL OR trade_volume >= 0)
        AND (open_price IS NULL OR open_price >= 0)
        AND (high_price IS NULL OR high_price >= 0)
        AND (low_price IS NULL OR low_price >= 0)
    ),
    -- 시세는 종목 행에만 붙는다. 섹션 문단에 값이 실리면 집계가 오염된다.
    ADD CONSTRAINT ck_infostock_daily_relation_quote_scope CHECK (
        relation_type = 'THEME_STOCK'
        OR (close_price IS NULL AND change_rate IS NULL AND trade_volume IS NULL
            AND open_price IS NULL AND high_price IS NULL AND low_price IS NULL
            AND theme_change_rate IS NULL)
    ),
    -- 2024년 이전 표에는 시가·고가·저가 칸이 없다. 종가·등락률·거래량은 함께 온다.
    ADD CONSTRAINT ck_infostock_daily_relation_quote_core CHECK (
        (close_price IS NULL) = (change_rate IS NULL)
        AND (close_price IS NULL) = (trade_volume IS NULL)
    );

CREATE INDEX IF NOT EXISTS ix_infostock_daily_relations_stock_change
    ON core.infostock_daily_relations (stock_id, change_rate DESC)
    WHERE relation_type = 'THEME_STOCK' AND change_rate IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_infostock_daily_relations_section_detail
    ON core.infostock_daily_relations (daily_post_revision_id, source_order)
    WHERE relation_type = 'SECTION_DETAIL';

COMMENT ON COLUMN core.infostock_daily_relations.paragraph_no IS
    '섹션 안 문단 순서. 0은 머리글, 1부터는 상세 문단이다.';
COMMENT ON COLUMN core.infostock_daily_relations.theme_change_rate IS
    '그날 그 테마의 등락률(%). 같은 표의 모든 종목 행에 동일하게 붙는다.';
COMMENT ON COLUMN core.infostock_daily_relations.change_rate IS
    '그날 그 종목의 등락률(%). 원문 표에 적힌 값이며 재계산하지 않는다.';

COMMIT;
