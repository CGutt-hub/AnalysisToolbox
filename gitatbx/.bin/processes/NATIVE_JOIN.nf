process NATIVE_JOIN {
    
    publishDir (
        path: { "${params.output_dir}/${participant_id}/.bin" },
        mode: 'copy',
        pattern: "*.parquet"
    )

    input:
        tuple val(participant_id), val(incoming_signals)
        val file_pattern

    output:
        tuple val(participant_id), path("*.parquet"), emit: merged_matrix

    script:
        def mixParser = new GroovyClassLoader().parseClass(moduleDir.resolve('../lib/mixParser.groovy').toFile())
        def cleanFilePaths = incoming_signals != null ? mixParser.extractCleanPaths(incoming_signals) : []
        def resolvedBaseName = "${participant_id}_${file_pattern}"
        def fileListGroovy = cleanFilePaths.collect { pathObj -> "'${pathObj.toString()}'" }.join(', ')

    """
    #!/usr/bin/env groovy

    @Grapes([
        @Grab(group='org.duckdb', module='duckdb_jdbc', version='0.10.0')
    ])
    import java.sql.DriverManager
    import java.io.File

    def participantId = "${participant_id}"
    def targetFile = "${resolvedBaseName}.parquet"
    def inputFiles = [${fileListGroovy}].findAll { filePath -> 
        def f = new File(filePath)
        return f.exists() && f.length() > 12 
    }

    if (inputFiles.isEmpty()) {
        System.err.println("[Native Join] ERROR: Zero valid Parquet input files provided for \${participantId}.")
        System.exit(1)
    }

    Class.forName("org.duckdb.DuckDBDriver")
    def conn = DriverManager.getConnection("jdbc:duckdb:")
    def stmt = conn.createStatement()

    try {
        def baseFile = inputFiles[0]
        def baseRowCountRs = stmt.executeQuery("SELECT COUNT(*) FROM read_parquet('\${baseFile}')")
        baseRowCountRs.next()
        long baseRowCount = baseRowCountRs.getLong(1)

        def selectClauses = []
        def joinedTablesSql = "FROM read_parquet('\${baseFile}') AS t0"
        
        def baseColsRs = stmt.executeQuery("DESCRIBE SELECT * FROM read_parquet('\${baseFile}')")
        def mergedColumns = []
        while (baseColsRs.next()) {
            mergedColumns << baseColsRs.getString("column_name")
        }

        mergedColumns.each { col -> selectClauses << "t0.\"\${col}\" AS \"\${col}\"" }

        inputFiles.tail().eachWithIndex { file, idx ->
            def tableAlias = "t\${idx + 1}"

            def countRs = stmt.executeQuery("SELECT COUNT(*) FROM read_parquet('\${file}')")
            countRs.next()
            long rowCount = countRs.getLong(1)
            if (rowCount != baseRowCount) {
                System.err.println("[Native Join] ERROR: Row height mismatch for participant \${participantId}! Base: \${baseRowCount}, File \${file}: \${rowCount}")
                System.exit(1)
            }

            def currColsRs = stmt.executeQuery("DESCRIBE SELECT * FROM read_parquet('\${file}')")
            def currCols = []
            while (currColsRs.next()) {
                currCols << currColsRs.getString("column_name")
            }

            joinedTablesSql += " POSITIONALLY JOIN read_parquet('\${file}') AS \${tableAlias}"

            currCols.each { col ->
                if (mergedColumns.contains(col)) {
                    def checkRs = stmt.executeQuery(\"\"\"
                        SELECT COUNT(*) 
                        FROM read_parquet('\${baseFile}') AS b
                        POSITIONALLY JOIN read_parquet('\${file}') AS c
                        WHERE b."\${col}" IS DISTINCT FROM c."\${col}"
                    \"\"\")
                    checkRs.next()
                    long mismatches = checkRs.getLong(1)

                    if (mismatches > 0) {
                        def newColName = "\${col}_stream_\${idx + 1}"
                        selectClauses << "\${tableAlias}.\"\${col}\" AS \"\${newColName}\""
                        mergedColumns << newColName
                    }
                } else {
                    selectClauses << "\${tableAlias}.\"\${col}\" AS \"\${col}\""
                    mergedColumns << col
                }
            }
        }

        def query = "COPY (SELECT \${selectClauses.join(', ')} \${joinedTablesSql}) TO '\${targetFile}' (FORMAT PARQUET, COMPRESSION 'GZIP')"
        stmt.execute(query)

        println "[Native Join] Successfully created merged matrix: \${targetFile}"

    } catch (Exception e) {
        System.err.println("[Native Join] ERROR: Horizontal join failed for \${participantId}: \${e.message}")
        e.printStackTrace()
        System.exit(1)
    } finally {
        stmt.close()
        conn.close()
    }
    """
}