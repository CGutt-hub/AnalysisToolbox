process NATIVE_CONCAT {
    
    publishDir (
        path: { "${params.output_dir}/${params.l2_folder ?: 'level_2_matrix'}" },
        mode: 'copy',
        pattern: "*.parquet"
    )

    input:
        val incoming_signals 
        val output_name          

    output:
        tuple val("group"), path("*.parquet"), emit: cohort_matrix

    script:
        def mixParser = new GroovyClassLoader().parseClass(moduleDir.resolve('../lib/mixParser.groovy').toFile())
        def cleanFilePaths = incoming_signals != null ? mixParser.extractCleanPaths(incoming_signals) : []
        def fileListGroovy = cleanFilePaths.collect { pathObj -> "'${pathObj.toString()}'" }.join(', ')

    """
    #!/usr/bin/env groovy

    @Grapes([
        @Grab(group='org.duckdb', module='duckdb_jdbc', version='0.10.0')
    ])
    import java.sql.DriverManager
    import java.io.File

    def targetFile = "${output_name}_concat.parquet"
    def inputFiles = [${fileListGroovy}].findAll { filePath -> 
        def f = new File(filePath)
        return f.exists() && f.length() > 12 
    }.sort()

    if (inputFiles.isEmpty()) {
        System.err.println("[Native Concat] ERROR: Zero valid input Parquet files provided. Aborting.")
        System.exit(1)
    }

    Class.forName("org.duckdb.DuckDBDriver")
    def conn = DriverManager.getConnection("jdbc:duckdb:")
    def stmt = conn.createStatement()

    try {
        def refFile = inputFiles[0]
        def refColsRs = stmt.executeQuery("DESCRIBE SELECT * FROM read_parquet('\${refFile}')")
        def refColumns = []
        while (refColsRs.next()) {
            refColumns << refColsRs.getString("column_name")
        }

        def refCountRs = stmt.executeQuery("SELECT COUNT(*) FROM read_parquet('\${refFile}')")
        refCountRs.next()
        long refRowCount = refCountRs.getLong(1)

        // Validate schema & row count across all cohort files (fail-fast)
        inputFiles.tail().each { file ->
            def colsRs = stmt.executeQuery("DESCRIBE SELECT * FROM read_parquet('\${file}')")
            def cols = []
            while (colsRs.next()) {
                cols << colsRs.getString("column_name")
            }

            if (cols != refColumns) {
                System.err.println("[Native Concat] ERROR: Schema mismatch in file \${file}!")
                System.err.println("Expected: \${refColumns}, Found: \${cols}")
                System.exit(1)
            }

            def countRs = stmt.executeQuery("SELECT COUNT(*) FROM read_parquet('\${file}')")
            countRs.next()
            if (countRs.getLong(1) != refRowCount) {
                System.err.println("[Native Concat] ERROR: Row count mismatch in file \${file}!")
                System.exit(1)
            }
        }

        // Infer participant_id directly from canonical filename prefix: Part_id_process_...
        def unionQueries = inputFiles.collect { filePath ->
            def fileName = new File(filePath).getName()
            // Extract everything before the second underscore or first underscore depending on pattern
            // Assumes standard pattern: 'PARTID_process_...'
            def pId = fileName.contains('_') ? fileName.split('_')[0] : fileName.replace('.parquet', '')
            
            return "SELECT '\${pId}' AS participant_id, * FROM read_parquet('\${filePath}')"
        }

        def stackedQuery = unionQueries.join(" UNION ALL ")
        def copyQuery = "COPY (\${stackedQuery}) TO '\${targetFile}' (FORMAT PARQUET, COMPRESSION 'GZIP')"
        
        stmt.execute(copyQuery)

        println "[Native Concat] Successfully concatenated cohort matrix using prefix IDs: \${targetFile}"

    } catch (Exception e) {
        System.err.println("[Native Concat] ERROR: Flat concatenation failed: \${e.message}")
        e.printStackTrace()
        System.exit(1)
    } finally {
        stmt.close()
        conn.close()
    }
    """
}