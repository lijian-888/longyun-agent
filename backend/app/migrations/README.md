# 隆耘业务数据库迁移

这里保存每个机构独立 PostgreSQL 业务数据库共用的 Alembic 迁移。

- `0001` 将当前存量表结构登记为兼容基线，不删除或重建旧表。
- `0002` 建立统一数据主链，并给存量核心表补充可空关联列。
- API 启动时使用机构的迁移账号执行 `upgrade head`。
- 多副本启动由 PostgreSQL advisory lock 串行化。

禁止手工修改已经在任何机构数据库执行过的 revision；后续变化必须新增 revision。
