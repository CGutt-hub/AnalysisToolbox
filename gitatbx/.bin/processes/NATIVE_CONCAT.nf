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

    exec:
        if (!output_name || output_name.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_CONCAT] FATAL: 'output_name' is required.")
        }

        def outputName  = output_name.toString().trim()
        def l2FolderVal = params.l2_folder ?: "${params.project_name}_l2"
        def launchDir   = workflow.launchDir.toFile()

        if (!params.output_dir || !params.project_name) {
            throw new IllegalStateException("[NATIVE_CONCAT] FATAL: Missing 'params.output_dir' or 'params.project_name'.")
        }

        def l2Dir = new java.io.File(launchDir, "${params.output_dir}/${l2FolderVal}")
        def targetBinDir = new java.io.File(l2Dir, ".bin")
        l2Dir.mkdirs()
        targetBinDir.mkdirs()

        def logFile = new java.io.File(targetBinDir, "${params.project_name}_l2.log")

        def SqlUtils            = null
        def BaseFileSystemUtils = null

        try {
            def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)

            def sqlUtilsFile = [
                moduleDir.resolve('../lib/SqlUtils.groovy').toFile(),
                moduleDir.resolve('../../lib/SqlUtils.groovy').toFile()
            ].find { java.io.File f -> f.exists() }

            def baseFsFile = [
                moduleDir.resolve('../lib/base/BaseFileSystemUtils.groovy').toFile(),
                moduleDir.resolve('../../lib/base/BaseFileSystemUtils.groovy').toFile()
            ].find { java.io.File f -> f.exists() }

            if (!sqlUtilsFile) throw new java.io.FileNotFoundException("[NATIVE_CONCAT] Missing SqlUtils.groovy.")
            if (!baseFsFile)  throw new java.io.FileNotFoundException("[NATIVE_CONCAT] Missing BaseFileSystemUtils.groovy.")

            gcl.addClasspath(sqlUtilsFile.parentFile.absolutePath)
            if (sqlUtilsFile.parentFile.parentFile.exists()) {
                gcl.addClasspath(sqlUtilsFile.parentFile.parentFile.absolutePath)
            }

            SqlUtils            = gcl.parseClass(sqlUtilsFile)
            BaseFileSystemUtils = gcl.parseClass(baseFsFile)

            BaseFileSystemUtils.appendLog(logFile, "[L2] [INFO] [NATIVE_CONCAT] Starting cohort concatenation for target '${outputName}'...")

            def trackingQueue = []
            if (incoming_signals instanceof Collection) {
                trackingQueue.addAll(incoming_signals)
            } else if (incoming_signals != null && incoming_signals.getClass().isArray()) {
                trackingQueue.addAll(incoming_signals as List)
            } else {
                trackingQueue.add(incoming_signals)
            }

            def cleanFiles = []
            trackingQueue.each { item ->
                if (item != null) {
                    def plainPath = item.toString().replaceAll(/[\[\]\"\']/, "").trim()
                    if (plainPath.endsWith('.parquet')) {
                        def f = new java.io.File(plainPath)
                        if (f.exists() && f.size() > 12) {
                            cleanFiles.add(f.absolutePath)
                        }
                    }
                }
            }
            cleanFiles = cleanFiles.unique().sort()

            if (cleanFiles.isEmpty()) {
                def err = "[NATIVE_CONCAT] ERROR: Zero valid input Parquet files provided for target '${outputName}'."
                BaseFileSystemUtils.appendLog(logFile, "[L2] [ERROR] ${err}")
                throw new RuntimeException(err)
            }

            def targetFile = "${outputName}_concat.parquet"
            def localDest  = new java.io.File(task.workDir.toFile(), targetFile)

            SqlUtils.withConnection { conn ->
                def unionQueries = cleanFiles.collect { String fp ->
                    def file     = new java.io.File(fp)
                    def fileName = file.getName()
                    def parts    = fileName.split('_')
                    def pId      = parts.length >= 2 ? "${parts[0]}_${parts[1]}" : fileName.take(fileName.lastIndexOf('.'))
                    def escFp    = SqlUtils.escapePath(fp)
                    return "SELECT '${pId}' AS participant_id, * FROM read_parquet('${escFp}')"
                }

                def stackedQuery = unionQueries.join(" UNION ALL ")
                def escDest      = SqlUtils.escapePath(localDest.absolutePath)
                def query        = "COPY (${stackedQuery}) TO '${escDest}' (FORMAT PARQUET, COMPRESSION 'ZSTD')"

                SqlUtils.executeQuery(conn, query)
            }

            def outputDest = new java.io.File(targetBinDir, targetFile)
            java.nio.file.Files.copy(localDest.toPath(), outputDest.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)
            BaseFileSystemUtils.appendLog(logFile, "[L2] [INFO] [NATIVE_CONCAT] Successfully concatenated cohort matrix: ${targetFile} (from ${cleanFiles.size()} files)")

        } catch (Throwable t) {
            def fatalErr = "[NATIVE_CONCAT] CRITICAL ERROR for target '${outputName}': ${t.message}"
            if (logFile != null && BaseFileSystemUtils != null) {
                BaseFileSystemUtils.appendLog(logFile, "[L2] [ERROR] ${fatalErr}")
            }
            throw new RuntimeException(fatalErr, t)
        }
}