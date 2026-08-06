package lib

import java.sql.Connection
import java.sql.Driver
import java.sql.Statement
import java.sql.ResultSet
import java.sql.ResultSetMetaData
import java.util.Properties
import java.lang.reflect.InvocationTargetException

class SqlUtils {

    private static final Driver DUCKDB_DRIVER

    static {
        try {
            Class<?> driverClass = Class.forName("org.duckdb.DuckDBDriver", true, SqlUtils.class.classLoader)
            DUCKDB_DRIVER = (Driver) driverClass.getDeclaredConstructor().newInstance()
        } catch (Throwable t) {
            Throwable cause = getRootCause(t)
            String msg = cause.getMessage() ?: cause.getClass().getName()
            System.err.println("[SqlUtils] CRITICAL: DuckDB Driver initialization failed: ${msg}")
            throw new ExceptionInInitializerError(cause)
        }
    }

    /**
     * Resolves the true root cause of an exception, unwrapping ExceptionInInitializerError
     * and InvocationTargetException wrapper types.
     */
    static Throwable getRootCause(Throwable throwable) {
        if (throwable == null) return null
        
        Throwable cause = throwable.getCause()
        if (cause == null || cause == throwable) {
            return throwable
        }
        
        if (throwable instanceof ExceptionInInitializerError || throwable instanceof InvocationTargetException) {
            return getRootCause(cause)
        }
        
        return getRootCause(cause)
    }

    /**
     * Creates a direct connection using the loaded DuckDB Driver instance,
     * bypassing DriverManager's ClassLoader restrictions.
     */
    static Connection getConnection() {
        return DUCKDB_DRIVER.connect("jdbc:duckdb:", new Properties())
    }

    /**
     * Executes closures within an isolated, ephemeral DuckDB connection context.
     * Uses untyped parameter to guarantee compatibility across all Groovy AST parsers.
     */
    @SuppressWarnings("unchecked")
    static <T> T withConnection(closure) {
        Connection conn = getConnection()
        try {
            return (T) closure.call(conn)
        } finally {
            if (conn != null && !conn.isClosed()) {
                conn.close()
            }
        }
    }

    /**
     * Executes closures using an existing Connection (preserves in-memory state across calls).
     */
    @SuppressWarnings("unchecked")
    static <T> T withConnection(Connection conn, closure) {
        return (T) closure.call(conn)
    }

    /**
     * Executes a write/DDL SQL statement on a new connection.
     */
    static void executeQuery(String sql) {
        withConnection { Connection conn ->
            executeQuery(conn, sql)
        }
    }

    /**
     * Executes a write/DDL SQL statement on an existing connection.
     */
    static void executeQuery(Connection conn, String sql) {
        Statement stmt = conn.createStatement()
        try {
            stmt.execute(sql)
        } finally {
            stmt.close()
        }
    }

    /**
     * Executes a SELECT/Read query on a new connection.
     */
    static List<Map<String, Object>> executeSelectQuery(String sql) {
        return withConnection { Connection conn ->
            return executeSelectQuery(conn, sql)
        }
    }

    /**
     * Executes a SELECT/Read query on an existing connection.
     */
    static List<Map<String, Object>> executeSelectQuery(Connection conn, String sql) {
        Statement stmt = conn.createStatement()
        try {
            ResultSet rs = stmt.executeQuery(sql)
            try {
                return extractResultSetRows(rs, new ArrayList<Map<String, Object>>())
            } finally {
                rs.close()
            }
        } finally {
            stmt.close()
        }
    }

    /**
     * Reads Parquet files directly via DuckDB on a new connection.
     */
    static List<Map<String, Object>> readParquet(List<String> filePaths) {
        return withConnection { Connection conn ->
            return readParquet(conn, filePaths)
        }
    }

    /**
     * Reads Parquet files directly via DuckDB on an existing connection.
     */
    static List<Map<String, Object>> readParquet(Connection conn, List<String> filePaths) {
        if (filePaths == null || filePaths.isEmpty()) {
            throw new IllegalArgumentException("[SqlUtils] File path list for Parquet read cannot be null or empty.")
        }
        String formattedPaths = filePaths.collect { String fp -> "'${escapePath(fp)}'" }.join(", ")
        String sql = "SELECT * FROM read_parquet([${formattedPaths}], union_by_name=true)"
        return executeSelectQuery(conn, sql)
    }

    /**
     * Constructs a unified Parquet export query supporting dynamic schema union across files using ZSTD compression.
     */
    static String buildParquetUnionQuery(List<String> filePaths, String outputDestination) {
        if (filePaths == null || filePaths.isEmpty()) {
            throw new IllegalArgumentException("[SqlUtils] File path list for Parquet union cannot be null or empty.")
        }
        
        String formattedPaths = filePaths.collect { String fp -> "'${escapePath(fp)}'" }.join(", ")
        String escDest        = escapePath(outputDestination)

        return "COPY (SELECT * FROM read_parquet([${formattedPaths}], union_by_name=true)) TO '${escDest}' (FORMAT PARQUET, COMPRESSION 'ZSTD')"
    }

    /**
     * Safely escapes single quotes for DuckDB SQL string literals.
     */
    static String escapeSql(String input) {
        if (input == null) return ""
        return input.replace("'", "''")
    }

    /**
     * Escapes single quotes and normalizes Windows backslashes for DuckDB file paths.
     */
    static String escapePath(String input) {
        if (input == null) return ""
        return input.replace('\\', '/').replace("'", "''")
    }

    /**
     * Recursively extracts ResultSet rows into Maps.
     */
    private static List<Map<String, Object>> extractResultSetRows(ResultSet rs, List<Map<String, Object>> accumulator) {
        if (!rs.next()) {
            return accumulator
        }

        ResultSetMetaData metaData = rs.getMetaData()
        int columnCount = metaData.getColumnCount()
        Map<String, Object> row = [:]

        (1..columnCount).each { int colIndex ->
            String colName = metaData.getColumnName(colIndex)
            Object val = rs.getObject(colIndex)
            row.put(colName, val)
        }

        accumulator.add(row)
        return extractResultSetRows(rs, accumulator)
    }
}