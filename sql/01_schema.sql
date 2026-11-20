-- =====================================================================
-- 中国网球公开赛赛事运营管理系统  数据库建表脚本
-- 数据库：MySQL 8.0+（字符集 utf8mb4）
-- 对应课程设计报告 4.5 节 数据库逻辑结构设计
-- 说明：
--   1. 报告中的表名 MATCH 是 MySQL 保留字，程序中统一使用反引号 `match`；
--   2. 报告 4.5 节共 6 张表，这里在 STAFF 中补充了登录失败计数与锁定时间
--      （对应 2.4 节"连续 3 次密码错误锁定账号 10 分钟"的非功能需求），
--      并新增 SYS_LOG 操作日志表（对应 2.4 节"所有删除和状态变更写入操作日志"）；
--   3. PLAYER 补充了 win_count / loss_count 两列，用于 FR-11"保存比分后自动
--      更新胜场、负场与胜率"。
-- =====================================================================

CREATE DATABASE IF NOT EXISTS `china_open`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_general_ci;

USE `china_open`;

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS `sys_log`;
DROP TABLE IF EXISTS `match_player`;
DROP TABLE IF EXISTS `match`;
DROP TABLE IF EXISTS `player`;
DROP TABLE IF EXISTS `referee`;
DROP TABLE IF EXISTS `court`;
DROP TABLE IF EXISTS `staff`;

SET FOREIGN_KEY_CHECKS = 1;

-- ---------------------------------------------------------------------
-- 表 1 工作人员表 STAFF（报告 4.5 节：员工编号、姓名、岗位、账号、密码哈希、角色、账号状态）
-- ---------------------------------------------------------------------
CREATE TABLE `staff` (
  `staff_id`      VARCHAR(8)  NOT NULL                COMMENT '员工编号，S+4位流水号',
  `name`          VARCHAR(30) NOT NULL                COMMENT '姓名',
  `post`          VARCHAR(30) NOT NULL                COMMENT '岗位名称',
  `login_name`    VARCHAR(30) NOT NULL                COMMENT '登录账号',
  `password_hash` VARCHAR(255) NOT NULL               COMMENT '加盐哈希后的密码',
  `role`          VARCHAR(20) NOT NULL                COMMENT '角色：admin系统管理员/scheduler赛事编排员/recorder成绩录入员',
  `status`        CHAR(1)     NOT NULL DEFAULT 'A'    COMMENT '账号状态：A启用 D停用',
  `fail_count`    INT         NOT NULL DEFAULT 0      COMMENT '连续登录失败次数',
  `locked_until`  DATETIME    NULL                    COMMENT '锁定截止时间',
  `created_at`    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`staff_id`),
  UNIQUE KEY `uk_staff_login_name` (`login_name`),
  CONSTRAINT `ck_staff_role`   CHECK (`role` IN ('admin','scheduler','recorder')),
  CONSTRAINT `ck_staff_status` CHECK (`status` IN ('A','D'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工作人员表';

-- ---------------------------------------------------------------------
-- 表 2 运动员表 PLAYER（报告表 10）
-- ---------------------------------------------------------------------
CREATE TABLE `player` (
  `player_id`   VARCHAR(8)  NOT NULL                  COMMENT '运动员编号，P+7位流水号',
  `name_cn`     VARCHAR(30) NOT NULL                  COMMENT '中文姓名，无则填外文名',
  `name_en`     VARCHAR(50) NULL                      COMMENT '外文姓名',
  `nation`      CHAR(3)     NOT NULL                  COMMENT '国籍代码，如 CHN/USA',
  `gender`      CHAR(1)     NOT NULL                  COMMENT 'M男 / F女',
  `rank_single` INT         NOT NULL DEFAULT 0        COMMENT '单打世界排名，无排名记0',
  `rank_double` INT         NULL                      COMMENT '双打世界排名',
  `association` VARCHAR(20) NULL                      COMMENT '所属协会',
  `wildcard`    CHAR(1)     NOT NULL DEFAULT 'N'      COMMENT 'Y持外卡 / N正常晋级',
  `win_count`   INT         NOT NULL DEFAULT 0        COMMENT '胜场数（成绩录入时自动累加）',
  `loss_count`  INT         NOT NULL DEFAULT 0        COMMENT '负场数（成绩录入时自动累加）',
  PRIMARY KEY (`player_id`),
  KEY `idx_player_nation` (`nation`),
  KEY `idx_player_rank` (`rank_single`),
  CONSTRAINT `ck_player_gender`   CHECK (`gender` IN ('M','F')),
  CONSTRAINT `ck_player_wildcard` CHECK (`wildcard` IN ('Y','N')),
  CONSTRAINT `ck_player_rank`     CHECK (`rank_single` BETWEEN 0 AND 2000)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='运动员表';

-- ---------------------------------------------------------------------
-- 表 3 裁判表 REFEREE
-- ---------------------------------------------------------------------
CREATE TABLE `referee` (
  `referee_id`  VARCHAR(6)  NOT NULL                  COMMENT '裁判编号，R+5位流水号',
  `name`        VARCHAR(30) NOT NULL                  COMMENT '姓名',
  `level`       VARCHAR(10) NOT NULL                  COMMENT '级别：金牌/银牌/铜牌/国际级',
  `cert_level`  VARCHAR(20) NULL                      COMMENT '持证等级',
  `duty_status` CHAR(1)     NOT NULL DEFAULT 'A'      COMMENT '排班状态：A可排 B已排 C停用',
  PRIMARY KEY (`referee_id`),
  CONSTRAINT `ck_referee_status` CHECK (`duty_status` IN ('A','B','C'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='裁判表';

-- ---------------------------------------------------------------------
-- 表 4 场地表 COURT
-- ---------------------------------------------------------------------
CREATE TABLE `court` (
  `court_id`   VARCHAR(6)   NOT NULL                  COMMENT '场地编号，C+5位流水号',
  `court_name` VARCHAR(30)  NOT NULL                  COMMENT '场地名称',
  `court_type` VARCHAR(20)  NOT NULL                  COMMENT '场地类型：中心场/副场/外围场/训练场',
  `capacity`   INT          NOT NULL DEFAULT 0        COMMENT '容纳人数',
  `status`     CHAR(1)      NOT NULL DEFAULT 'A'      COMMENT '场地状态：A可用 M维护中',
  PRIMARY KEY (`court_id`),
  CONSTRAINT `ck_court_status` CHECK (`status` IN ('A','M'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='场地表';

-- ---------------------------------------------------------------------
-- 表 5 比赛表 MATCH（报告表 9）
-- ---------------------------------------------------------------------
CREATE TABLE `match` (
  `match_id`     VARCHAR(10) NOT NULL                 COMMENT '比赛编号，M+年份后两位+4位流水号（如 M260001）',
  `event_code`   CHAR(2)     NOT NULL                 COMMENT '项目代码：MS/WS/MD/WD/XD',
  `round_code`   CHAR(2)     NOT NULL                 COMMENT '轮次代码：Q/R1/R2/QF/SF/F',
  `court_id`     VARCHAR(6)  NOT NULL                 COMMENT '比赛场地，参照场地表',
  `referee_id`   VARCHAR(6)  NOT NULL                 COMMENT '主裁判，参照裁判表',
  `match_date`   DATE        NOT NULL                 COMMENT '比赛日期，须在赛事周期内',
  `start_time`   CHAR(5)     NOT NULL                 COMMENT '开始时间，HH:MM 24小时制',
  `duration_min` INT         NOT NULL DEFAULT 120     COMMENT '预计时长（分钟），用于冲突校验',
  `set_score`    VARCHAR(5)  NULL                     COMMENT '盘分，形如 2:1，未结束为空',
  `game_score`   VARCHAR(11) NULL                     COMMENT '局分明细，未结束为空',
  `actual_min`   INT         NULL                     COMMENT '实际比赛时长（分钟），用于统计',
  `status`       CHAR(1)     NOT NULL DEFAULT 'N'     COMMENT '比赛状态：N未开始 P进行中 F已结束',
  `created_at`   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`match_id`),
  KEY `idx_match_date` (`match_date`),
  KEY `idx_match_court` (`court_id`),
  KEY `idx_match_referee` (`referee_id`),
  CONSTRAINT `fk_match_court`   FOREIGN KEY (`court_id`)   REFERENCES `court` (`court_id`),
  CONSTRAINT `fk_match_referee` FOREIGN KEY (`referee_id`) REFERENCES `referee` (`referee_id`),
  UNIQUE KEY `uk_match_court_slot` (`court_id`, `match_date`, `start_time`),
  CONSTRAINT `ck_match_event`  CHECK (`event_code` IN ('MS','WS','MD','WD','XD')),
  CONSTRAINT `ck_match_round`  CHECK (`round_code` IN ('Q','R1','R2','QF','SF','F')),
  CONSTRAINT `ck_match_status` CHECK (`status` IN ('N','P','F'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='比赛表';

-- ---------------------------------------------------------------------
-- 表 6 参赛表 MATCH_PLAYER（比赛与运动员 m:n 联系的关联表）
-- ---------------------------------------------------------------------
CREATE TABLE `match_player` (
  `match_id`  VARCHAR(10) NOT NULL                    COMMENT '比赛编号，参照比赛表',
  `player_id` VARCHAR(8)  NOT NULL                    COMMENT '运动员编号，参照运动员表',
  `team_no`   TINYINT     NOT NULL                    COMMENT '队伍序号：1或2',
  PRIMARY KEY (`match_id`, `player_id`),
  KEY `idx_mp_player` (`player_id`),
  CONSTRAINT `fk_mp_match`  FOREIGN KEY (`match_id`)  REFERENCES `match` (`match_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_mp_player` FOREIGN KEY (`player_id`) REFERENCES `player` (`player_id`),
  CONSTRAINT `ck_mp_team`   CHECK (`team_no` IN (1,2))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='参赛表';

-- ---------------------------------------------------------------------
-- 表 7 操作日志表 SYS_LOG（2.4 节非功能需求：删除与状态变更留痕）
-- ---------------------------------------------------------------------
CREATE TABLE `sys_log` (
  `log_id`     BIGINT       NOT NULL AUTO_INCREMENT   COMMENT '日志流水号',
  `staff_id`   VARCHAR(8)   NULL                      COMMENT '操作人员工编号',
  `staff_name` VARCHAR(30)  NULL                      COMMENT '操作人姓名',
  `action`     VARCHAR(30)  NOT NULL                  COMMENT '操作类型：登录/新增/修改/删除/状态变更/录入成绩',
  `target`     VARCHAR(60)  NULL                      COMMENT '操作对象，如 match:M20260001',
  `detail`     VARCHAR(255) NULL                      COMMENT '变更说明（变更前后的值）',
  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
  PRIMARY KEY (`log_id`),
  KEY `idx_log_time` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='操作日志表';

-- ---------------------------------------------------------------------
-- 视图：比赛详情（编排/成绩/查询三个模块共用，减少重复 JOIN）
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW `v_match_detail` AS
SELECT
  m.`match_id`,
  m.`event_code`,
  m.`round_code`,
  m.`court_id`,
  c.`court_name`,
  c.`status`      AS `court_status`,
  m.`referee_id`,
  r.`name`        AS `referee_name`,
  m.`match_date`,
  m.`start_time`,
  m.`duration_min`,
  m.`set_score`,
  m.`game_score`,
  m.`actual_min`,
  m.`status`
FROM `match` m
JOIN `court`   c ON c.`court_id`   = m.`court_id`
JOIN `referee` r ON r.`referee_id` = m.`referee_id`;
