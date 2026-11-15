def runtime_status(connection) -> dict:
    """以同一条查询读取执行与导入状态，供停服前检查。"""
    rows = connection.execute("""SELECT 'jobs' AS category,status,count(*) AS total
        FROM plan_jobs GROUP BY status UNION ALL
        SELECT 'imports',status,count(*) FROM knowledge_sources GROUP BY status UNION ALL
        SELECT 'cleanup','pending',count(*) FROM file_cleanup""").fetchall()
    result = {"jobs": {}, "imports": {}, "cleanup": {}}
    for row in rows:
        result[row[0]][row[1]] = row[2]
    result["idle"] = not (
        sum(result["jobs"].get(state, 0) for state in ("queued", "running"))
        + sum(result["imports"].get(state, 0) for state in ("pending", "processing"))
        + result["cleanup"].get("pending", 0)
    )
    return result
