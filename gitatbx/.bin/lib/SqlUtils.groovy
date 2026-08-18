import java.security.CodeSource
import java.net.URL
import java.net.URLClassLoader
import java.sql.Connection
import java.sql.Driver
import java.sql.ResultSet
import java.sql.Statement
import java.sql.SQLException
import java.util.Properties

class SqlUtils {

    static File resolveJarFile() {
        try {
            CodeSource codeSource = SqlUtils.class.protectionDomain?.codeSource
            if (codeSource != null && codeSource.location != null) {
                File classLocation = new File(codeSource.location.toURI())
                File currentSearchDir = classLocation.isDirectory() ? classLocation : classLocation.parentFile
                
                while (currentSearchDir != null) {
                    File candidateLib = new File(currentSearchDir, "lib")
                    if (candidateLib.exists() && candidateLib.isDirectory()) {
                        File targetJar = new File(candidateLib, "duckdb_jdbc.jar")
                        if (targetJar.exists()) return targetJar
                        
                        File wildcardMatch = candidateLib.listFiles()?.find { 
                            it.name.toLowerCase().contains("duckdb") && it.name.toLowerCase().endsWith(".jar") 
                        }
                        if (wildcardMatch) return wildcardMatch
                    }
                    currentSearchDir = currentSearchDir.parentFile
                }
            }
        } catch (Throwable ignored) {}

        File currentDir = new File(System.getProperty("user.dir"))
        while (currentDir != null) {
            File candidateLib = new File(currentDir, "lib")
            if (candidateLib.exists() && candidateLib.isDirectory()) {
                File targetJar = new File(candidateLib, "duckdb_jdbc.jar")
                if (targetJar.exists()) return targetJar
                
                File wildcardMatch = candidateLib.listFiles()?.find { 
                    it.name.toLowerCase().contains("duckdb") && it.name.toLowerCase().endsWith(".jar") 
                }
                if (wildcardMatch) return wildcardMatch
            }
            currentDir = currentDir.parentFile
        }

        throw new FileNotFoundException("[SqlUtils] FATAL: Failed to locate 'duckdb_jdbc.jar' in any parent 'lib/' directory hierarchy.")
    }

    static Connection getConnection() {
        try {
            File jarFile = resolveJarFile()
            if (!jarFile || !jarFile.exists()) {
                throw new FileNotFoundException("[SqlUtils] FATAL: Resolved DuckDB JAR file does not exist on disk.")
            }

            URL[] urls = [jarFile.toURI().toURL()] as URL[]
            URLClassLoader classLoader = new URLClassLoader(urls, Thread.currentThread().contextClassLoader)

            Class<?> driverClass = Class.forName("org.duckdb.DuckDBDriver", true, classLoader)
            Driver driver = (Driver) driverClass.getDeclaredConstructor().newInstance()

            Properties props = new Properties()
            Connection conn = driver.connect("jdbc:duckdb:", props)

            if (!conn) {
                throw new SQLException("[SqlUtils] FATAL: DuckDB driver returned a null connection instance.")
            }
            return conn
        } catch (Throwable t) {
            throw new RuntimeException("[SqlUtils] FATAL: Failed to initialize DuckDB connection via dynamic class loader: ${t.message}", t)
        }
    }

    static void withConnection(Closure closure) {
        Connection conn = getConnection()
        try {
            closure(conn)
        } catch (Throwable t) {
            Throwable root = getRootCause(t)
            throw new RuntimeException("[SqlUtils] FATAL: DuckDB transaction execution failed: ${root.message}", root)
        } finally {
            try {
                conn?.close()
            } catch (Throwable ignored) {}
        }
    }

    static void executeQuery(Connection conn, String query) {
        if (!query || query.trim().isEmpty()) {
            throw new IllegalArgumentException("[SqlUtils] FATAL: Attempted to execute null or empty SQL query string.")
        }
        Statement stmt = conn.createStatement()
        try {
            stmt.execute(query)
        } finally {
            try {
                stmt?.close()
            } catch (Throwable ignored) {}
        }
    }

    static void executeQuery(String query) {
        withConnection { Connection conn ->
            executeQuery(conn, query)
        }
    }

    static List<Map<String, Object>> executeSelectQuery(Connection conn, String query) {
        if (!query || query.trim().isEmpty()) {
            throw new IllegalArgumentException("[SqlUtils] FATAL: Attempted to execute null or empty SELECT query string.")
        }
        Statement stmt = conn.createStatement()
        List<Map<String, Object>> results = []
        try {
            ResultSet rs = stmt.executeQuery(query)
            def metaData = rs.getMetaData()
            int columnCount = metaData.getColumnCount()
            
            while (rs.next()) {
                Map<String, Object> row = [:]
                for (int i = 1; i <= columnCount; i++) {
                    row.put(metaData.getColumnName(i), rs.getObject(i))
                }
                results.add(row)
            }
        } finally {
            try {
                stmt?.close()
            } catch (Throwable ignored) {}
        }
        return results
    }

    static List<Map<String, Object>> executeSelectQuery(String query) {
        Connection conn = getConnection()
        try {
            return executeSelectQuery(conn, query)
        } finally {
            try {
                conn?.close()
            } catch (Throwable ignored) {}
        }
    }

    static String escapeSql(String input) {
        if (input == null) {
            throw new IllegalArgumentException("[SqlUtils] FATAL: Cannot escape null SQL input string.")
        }
        return input.replace("'", "''")
    }

    static String escapePath(String input) {
        if (input == null) {
            throw new IllegalArgumentException("[SqlUtils] FATAL: Cannot escape null file path.")
        }
        String normalized = input.replace('\\', '/')
        return escapeSql(normalized)
    }

    /**
     * Vertical row stacking query generator for NATIVE_CONCAT.
     */
    static String buildParquetUnionQuery(List<String> filePaths, String outputAbsolutePath, boolean distinct) {
        if (filePaths == null || filePaths.isEmpty()) {
            throw new IllegalArgumentException("[SqlUtils] FATAL: Cannot build union query for empty file list.")
        }
        
        def escapedPaths = filePaths.collect { String path -> "'${escapePath(path)}'" }.join(", ")
        def escapedOut   = escapePath(outputAbsolutePath)
        def selectClause = distinct ? "SELECT DISTINCT * " : "SELECT * "

        return "COPY (${selectClause}FROM read_parquet([${escapedPaths}], union_by_name = true)) TO '${escapedOut}' (FORMAT PARQUET, COMPRESSION 'ZSTD')"
    }

    /**
     * Explicit Horizontal Join Query Engine for NATIVE_JOIN.
     * @param conn Active DuckDB JDBC connection
     * @param filePaths List of input Parquet file paths
     * @param outputAbsolutePath Target output Parquet path
     * @param joinKey Explicit column name to align on (e.g. 'time', 'epoch_id', 'condition')
     * @param scaleTag Explicit scale mode ('continuous' or 'discrete')
     */
    static String buildParquetJoinQuery(
        Connection conn, 
        List<String> filePaths, 
        String outputAbsolutePath, 
        String joinKey, 
        String scaleTag
    ) {
        if (filePaths == null || filePaths.isEmpty()) {
            throw new IllegalArgumentException("[SqlUtils] FATAL: Cannot build join query for empty file list.")
        }
        if (!joinKey || joinKey.trim().isEmpty()) {
            throw new IllegalArgumentException("[SqlUtils] FATAL: An explicit 'joinKey' parameter must be provided.")
        }
        
        def mode = scaleTag ? scaleTag.trim().toLowerCase() : ""
        if (mode != "continuous" && mode != "discrete") {
            throw new IllegalArgumentException("[SqlUtils] FATAL: Invalid 'scaleTag' ('${scaleTag}'). Must be explicitly set to 'continuous' or 'discrete'.")
        }

        def escapedKey = escapeSql(joinKey.trim())

        if (filePaths.size() == 1) {
            def escapedFile = escapePath(filePaths[0])
            def escapedOut  = escapePath(outputAbsolutePath)
            return "COPY (SELECT * FROM read_parquet('${escapedFile}')) TO '${escapedOut}' (FORMAT PARQUET, COMPRESSION 'ZSTD')"
        }

        // 1. Inspect schemas and verify joinKey exists in all input files
        List<List<String>> fileColumns = []
        for (int i = 0; i < filePaths.size(); i++) {
            def escaped = escapePath(filePaths[i])
            List<Map<String, Object>> describeRes = executeSelectQuery(conn, "DESCRIBE SELECT * FROM read_parquet('${escaped}')")
            List<String> cols = describeRes.collect { it.get("column_name")?.toString() }.findAll { it != null }
            
            if (!cols.contains(escapedKey)) {
                throw new IllegalStateException("Schema Error: Explicit join key '${escapedKey}' was not found in input file #${i + 1} (${filePaths[i]}).")
            }
            fileColumns.add(cols)
        }

        // 2. Build SELECT list (Keep key once from t1, deduct duplicate column names across secondary files)
        List<String> selectExpressions = []
        Set<String> seenColumns = new HashSet<>()

        for (int i = 0; i < filePaths.size(); i++) {
            String alias = "t${i + 1}"
            List<String> cols = fileColumns[i]

            for (String col : cols) {
                if (!seenColumns.contains(col)) {
                    seenColumns.add(col)
                    def escapedCol = escapeSql(col)
                    selectExpressions.add("${alias}.\"${escapedCol}\"")
                }
            }
        }

        // 3. Construct JOIN Query based on discrete vs continuous scale mode
        def firstEscapedPath = escapePath(filePaths[0])
        StringBuilder fromClause = new StringBuilder()
        fromClause.append("read_parquet('${firstEscapedPath}') t1")

        // Continuous scale uses FULL OUTER JOIN (preserves all metric timestamps, pads non-overlapping points with NaN)
        // Discrete scale uses LEFT OUTER JOIN (matches categorical equivalence classes, broadcasts condition metadata)
        String sqlJoinType = (mode == "continuous") ? "FULL OUTER JOIN" : "LEFT OUTER JOIN"

        for (int i = 1; i < filePaths.size(); i++) {
            String alias = "t${i + 1}"
            def escapedPath = escapePath(filePaths[i])
            fromClause.append(" ${sqlJoinType} read_parquet('${escapedPath}') ${alias}")
            fromClause.append(" ON t1.\"${escapedKey}\" = ${alias}.\"${escapedKey}\"")
        }

        def selectClauseStr = selectExpressions.join(", ")
        def escapedOut      = escapePath(outputAbsolutePath)

        return "COPY (SELECT ${selectClauseStr} FROM ${fromClause.toString()}) TO '${escapedOut}' (FORMAT PARQUET, COMPRESSION 'ZSTD')"
    }

    static Throwable getRootCause(Throwable t) {
        Throwable cause = t
        while (cause.cause != null && cause != cause.cause) {
            cause = cause.cause
        }
        return cause
    }
}