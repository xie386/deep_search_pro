-- ============================================================
-- 智选情报官 · 公司侧业务库 Schema（M1）
-- 数据库：deep_search_pro
-- 领域示例：智能办公 / 企业协作软件（呼应文档「飞书 vs 钉钉」示例）
-- 说明：网搜公开情报实时检索不落库；仅「本公司数据 / 关注竞品清单」落库
-- ============================================================

SET FOREIGN_KEY_CHECKS=0;
DROP TABLE IF EXISTS our_sales;
DROP TABLE IF EXISTS our_products;
DROP TABLE IF EXISTS competitors;
SET FOREIGN_KEY_CHECKS=1;
CREATE TABLE our_products (
    id            INT PRIMARY KEY AUTO_INCREMENT,
    product_name  VARCHAR(100)  NOT NULL COMMENT '产品名称',
    category      VARCHAR(50)   NOT NULL COMMENT '品类，如 IM/文档/会议/OA',
    price         DECIMAL(10,2) NOT NULL COMMENT '标准版年费单价（元/人/年），0 表示免费',
    billing_unit  VARCHAR(20)   NOT NULL COMMENT '计费单位',
    core_features TEXT          COMMENT '核心功能点（逗号分隔或自然语言）',
    target_scale  VARCHAR(50)   COMMENT '主打客户规模，如 中小企业/中大型',
    launch_date   DATE          COMMENT '产品首发日期',
    updated_at    DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本公司产品矩阵';

-- 本公司销售 / 营收数据
DROP TABLE IF EXISTS our_sales;
CREATE TABLE our_sales (
    id           INT PRIMARY KEY AUTO_INCREMENT,
    product_id   INT           NOT NULL COMMENT '关联 our_products.id',
    month        CHAR(7)       NOT NULL COMMENT '统计月份，如 2026-01',
    region       VARCHAR(30)   COMMENT '区域，如 华东/华南/华北/海外',
    revenue      DECIMAL(12,2) NOT NULL COMMENT '当月营收（元）',
    seats_sold   INT           COMMENT '当月新增付费席位数',
    FOREIGN KEY (product_id) REFERENCES our_products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本公司销售营收';

-- 关注的竞品清单（F7 竞品库管理用）
DROP TABLE IF EXISTS competitors;
CREATE TABLE competitors (
    id          INT PRIMARY KEY AUTO_INCREMENT,
    comp_name   VARCHAR(100) NOT NULL COMMENT '竞品公司/产品名',
    category    VARCHAR(50)  COMMENT '品类',
    website     VARCHAR(200) COMMENT '官网',
    is_competitor BOOLEAN   DEFAULT TRUE COMMENT '是否直接竞品',
    note        TEXT         COMMENT '备注：定位/差异化观察',
    -- C 折中方案：轻量扩展列。记录竞品对标本公司哪些产品（多对多映射），
    -- 如 [1,4] 表示同时对标云协IM与云协OA。重度的 attributes/dimensions 泛化留到个人侧。
    mapped_product_ids JSON COMMENT '映射的本司产品ID列表，如 [1,3]',
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='关注竞品清单';

-- ============================================================
-- 样例数据（本公司 = 「云协科技」，一款国产企业协作套件）
-- ============================================================

INSERT INTO our_products
    (product_name, category, price, billing_unit, core_features, target_scale, launch_date)
VALUES
    ('云协IM',     'IM',        360.00, '人/年', '即时通讯,已读回执,万人群,安全合规',        '中小企业',     '2021-03-15'),
    ('云协文档',   '文档',      480.00, '人/年', '在线协作文档,表格,知识库,模板中心',        '中大型',     '2021-09-01'),
    ('云协会议',   '会议',      300.00, '人/年', '高清会议,屏幕共享,实时字幕,会议纪要',      '全规模',     '2022-01-10'),
    ('云协OA',     'OA',        600.00, '人/年', '审批流,考勤,人事,报表,低代码搭建',        '中大型',     '2022-06-20'),
    ('云协免费版', 'IM',          0.00, '人/年', '基础IM,100人上限,有限存储',               '中小企业',   '2021-03-15');

INSERT INTO our_sales
    (product_id, month, region, revenue, seats_sold)
VALUES
    (1, '2026-01', '华东', 1280000.00, 3200),
    (1, '2026-01', '华南',  860000.00, 2150),
    (1, '2026-02', '华东', 1350000.00, 3380),
    (2, '2026-01', '华北',  940000.00, 1800),
    (2, '2026-02', '华北', 1010000.00, 1950),
    (3, '2026-01', '华东',  620000.00, 2400),
    (3, '2026-02', '海外',  410000.00, 1500),
    (4, '2026-01', '华南',  780000.00, 1100),
    (4, '2026-02', '华南',  820000.00, 1180);

INSERT INTO competitors
    (comp_name, category, website, is_competitor, note, mapped_product_ids)
VALUES
    ('飞书',   '协作套件', 'https://www.feishu.cn',  TRUE, '字节系，强在文档与OKR，设计感强，主攻中大型与互联网', '[1,2,4]'),
    ('钉钉',   '协作套件', 'https://www.dingtalk.com', TRUE, '阿里系，强在OA与生态，覆盖广大中小企业与政企', '[1,4]'),
    ('企业微信', '协作套件', 'https://work.weixin.qq.com', TRUE, '腾讯系，强在微信生态连接，私域运营场景突出', '[1]'),
    ('Slack',  'IM',       'https://slack.com',    FALSE, '海外标杆，国内合规与本地化弱，作为对标参考', '[1]'),
    ('Notion', '文档',      'https://www.notion.so', FALSE, '海外文档知识库标杆，AI能力突出，国内访问受限', '[2]');
