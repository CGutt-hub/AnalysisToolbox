import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

class TableManager {

    private static List<Object> flattenCollection(Object input) {
        if (input == null) return []
        if (input instanceof Collection) {
            List<Object> res = []
            ((Collection) input).each { res.addAll(flattenCollection(it)) }
            return res
        }
        return [input]
    }

    static String deriveIdentifier(Object incomingSignals) {
        def rawList = flattenCollection(incomingSignals)
        if (rawList.isEmpty() || rawList[0] == null) return null

        try {
            String fileName = new java.io.File(rawList[0].toString()).name
            if (!fileName || fileName.trim().isEmpty()) return null

            def rawStem = fileName.replaceAll(/\.[^.]+$/, '')
            def prefixMatcher = rawStem =~ /^([a-zA-Z0-9]+_[0-9]+)/
            return prefixMatcher.find() ? prefixMatcher.group(1) : null
        } catch (Throwable ignored) {
            return null
        }
    }

    /**
     * Extracts signal suffix identifier from incoming sample (NATIVE_CHANNEL_L2).
     */
    static String deriveSignalSuffix(Object incomingSignal) {
        def rawList = flattenCollection(incomingSignal)
        if (rawList.isEmpty() || rawList[0] == null) return null

        try {
            String fileName = new java.io.File(rawList[-1].toString()).name
            if (!fileName || !fileName.endsWith('.parquet')) return null

            def stem = fileName.replaceAll(/\.parquet$/, '')
            def suffix = stem.replaceFirst(/^[^_]+_[^_]+_/, '')
            if (suffix == stem) {
                suffix = stem.replaceFirst(/^[^_]+_/, '')
            }
            return suffix.trim().isEmpty() ? null : suffix
        } catch (Throwable ignored) {
            return null
        }
    }

    /**
     * Resolves matching signal files across participant output directories.
     */
    static List<String> resolveCohortSignalFiles(Object participantDirs, String signalSuffix, java.io.File launchDir) {
        def dirList = flattenCollection(participantDirs)
        if (dirList.isEmpty()) return []

        def cleanSuffix = signalSuffix.endsWith('.parquet') ? signalSuffix : "${signalSuffix}.parquet"
        List<String> resolvedFiles = []

        for (Object rawDir : dirList) {
            if (rawDir == null) continue
            def relPathStr = rawDir.toString().trim()
            def pDir = relPathStr.startsWith('/') ? new java.io.File(relPathStr) : new java.io.File(launchDir, relPathStr)

            if (!pDir.exists() || !pDir.isDirectory()) continue

            def searchDirs = [pDir, new java.io.File(pDir, ".bin")]
            java.io.File matchedFile = null
            for (java.io.File candDir : searchDirs) {
                if (candDir.exists() && candDir.isDirectory()) {
                    matchedFile = candDir.listFiles().find { java.io.File diskFile ->
                        diskFile.isFile() && diskFile.name.endsWith(".parquet") && diskFile.name.endsWith(cleanSuffix)
                    }
                    if (matchedFile) break
                }
            }

            if (matchedFile && matchedFile.exists() && matchedFile.length() > 0) {
                resolvedFiles.add(matchedFile.absolutePath)
            }
        }

        return resolvedFiles
    }

    static List<String> validateInputs(Object incomingSignals) {
        def rawList = flattenCollection(incomingSignals)
        if (rawList.isEmpty()) return null

        List<String> validPaths = []

        for (Object sig : rawList) {
            if (sig == null) return null

            Path p = (sig instanceof Path) ? (Path) sig : Paths.get(sig.toString().trim())
            if (!p.isAbsolute()) {
                p = p.toAbsolutePath().normalize()
            }

            if (Files.exists(p) && Files.isRegularFile(p) && Files.size(p) > 0) {
                validPaths.add(p.toString())
            } else {
                return null
            }
        }

        return validPaths
    }

    /**
     * Executes DuckDB Horizontal Join Operation (NATIVE_JOIN).
     */
    static String executeJoin(List<String> cleanFiles, String localOutputPath, String joinKey, String scaleTag) {
        try {
            SqlUtils.withConnection { java.sql.Connection conn ->
                def joinQuery = SqlUtils.buildParquetJoinQuery(conn, cleanFiles, localOutputPath, joinKey, scaleTag)
                SqlUtils.executeQuery(conn, joinQuery)
            }
            def out = new java.io.File(localOutputPath)
            if (out.exists() && out.length() > 0) {
                return null
            } else {
                return "Output Parquet file missing or empty after join execution: ${localOutputPath}"
            }
        } catch (Throwable e) {
            def causeMsg = e.cause ? " (Cause: ${e.cause.message})" : ""
            return "DuckDB Join Engine Error [${e.getClass().simpleName}]: ${e.message}${causeMsg}"
        }
    }

    /**
     * Executes DuckDB Vertical Row Concat Operation (NATIVE_CONCAT).
     */
    static String executeConcat(List<String> cleanFiles, String localOutputPath, boolean distinct) {
        try {
            SqlUtils.withConnection { java.sql.Connection conn ->
                def concatQuery = SqlUtils.buildParquetUnionQuery(cleanFiles, localOutputPath, distinct)
                SqlUtils.executeQuery(conn, concatQuery)
            }
            def out = new java.io.File(localOutputPath)
            if (out.exists() && out.length() > 0) {
                return null
            } else {
                return "Output Parquet file missing or empty after concat execution: ${localOutputPath}"
            }
        } catch (Throwable e) {
            def causeMsg = e.cause ? " (Cause: ${e.cause.message})" : ""
            return "DuckDB Concat Engine Error [${e.getClass().simpleName}]: ${e.message}${causeMsg}"
        }
    }

    /**
     * Executes DuckDB Cohort Concatenation with Key Column Addition (NATIVE_CHANNEL_L2).
     */
    static String executeCohortConcatenation(List<String> cleanFiles, String localOutputPath, String keyColumnName = "participant_id") {
        try {
            SqlUtils.withConnection { java.sql.Connection conn ->
                List<String> selectQueries = []
                for (String file : cleanFiles) {
                    def pId = deriveIdentifier(file)
                    if (!pId) {
                        def fileName = new java.io.File(file).name
                        def matcher = (fileName =~ /^([^_]+_[^_]+)/)
                        pId = matcher.find() ? matcher.group(1) : fileName.replaceAll(/\..*$/, '')
                    }
                    def escFile = SqlUtils.escapePath(file)
                    def escPid  = SqlUtils.escapeSql(pId)
                    selectQueries.add("SELECT '${escPid}' AS ${keyColumnName}, * FROM read_parquet('${escFile}')")
                }
                def unionQuery = selectQueries.join(" UNION ALL ")
                def escOut     = SqlUtils.escapePath(localOutputPath)
                def copyQuery  = "COPY (${unionQuery}) TO '${escOut}' (FORMAT PARQUET, COMPRESSION 'ZSTD')"

                SqlUtils.executeQuery(conn, copyQuery)
            }
            def out = new java.io.File(localOutputPath)
            if (out.exists() && out.length() > 0) {
                return null
            } else {
                return "Output Parquet file missing or empty after cohort concat execution: ${localOutputPath}"
            }
        } catch (Throwable e) {
            def causeMsg = e.cause ? " (Cause: ${e.cause.message})" : ""
            return "DuckDB Cohort Concat Engine Error [${e.getClass().simpleName}]: ${e.message}${causeMsg}"
        }
    }

    static String executeOperation(List<String> cleanFiles, String localOutputPath, boolean distinct) {
        return executeConcat(cleanFiles, localOutputPath, distinct)
    }
}