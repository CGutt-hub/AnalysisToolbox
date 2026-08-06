process NATIVE_JOIN {

    publishDir (
        path: { "${params.output_dir}/${params.project_name}_l1/${participant_id}/.bin" },
        mode: 'copy',
        pattern: "*.parquet"
    )

    input:
        tuple val(participant_id), path(incoming_signals)
        val file_pattern

    output:
        tuple val(participant_id), path("*.parquet"), emit: merged_matrix

    exec:
        if (!participant_id || participant_id.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_JOIN] FATAL: Participant 'id' is required.")
        }
        if (!file_pattern || file_pattern.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_JOIN] FATAL: 'file_pattern' is required.")
        }

        def currentId  = participant_id.toString().trim()
        def outputName = file_pattern.toString().trim()
        def launchDir  = workflow.launchDir.toFile()

        if (!params.output_dir || !params.project_name) {
            throw new IllegalStateException("[NATIVE_JOIN] FATAL: Missing 'params.output_dir' or 'params.project_name'.")
        }

        def participantDir = new java.io.File(launchDir, "${params.output_dir}/${params.project_name}_l1/${currentId}")
        def targetBinDir   = new java.io.File(participantDir, ".bin")
        participantDir.mkdirs()
        targetBinDir.mkdirs()

        def participantLog = new java.io.File(targetBinDir, "${currentId}.log")

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

            if (!sqlUtilsFile) throw new java.io.FileNotFoundException("[NATIVE_JOIN] Missing SqlUtils.groovy.")
            if (!baseFsFile)  throw new java.io.FileNotFoundException("[NATIVE_JOIN] Missing BaseFileSystemUtils.groovy.")

            gcl.addClasspath(sqlUtilsFile.parentFile.absolutePath)
            if (sqlUtilsFile.parentFile.parentFile.exists()) {
                gcl.addClasspath(sqlUtilsFile.parentFile.parentFile.absolutePath)
            }

            SqlUtils            = gcl.parseClass(sqlUtilsFile)
            BaseFileSystemUtils = gcl.parseClass(baseFsFile)

            BaseFileSystemUtils.appendLog(participantLog, "[L1] [INFO] [NATIVE_JOIN] Joining specified signal files for '${currentId}' into '${outputName}'...")

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
            cleanFiles = cleanFiles.unique()

            if (cleanFiles.isEmpty()) {
                def err = "[NATIVE_JOIN] ERROR: Zero valid input Parquet files provided for '${currentId}' under target '${outputName}'."
                BaseFileSystemUtils.appendLog(participantLog, "[L1] [ERROR] ${err}")
                throw new RuntimeException(err)
            }

            def targetFile = "${currentId}_${outputName}.parquet"
            def localDest  = new java.io.File(task.workDir.toFile(), targetFile)

            SqlUtils.withConnection { conn ->
                def baseFile     = cleanFiles[0]
                def selectClause = "t0.*"
                def joinClause   = "read_parquet('${SqlUtils.escapePath(baseFile)}') AS t0"

                cleanFiles.tail().eachWithIndex { String fp, int idx ->
                    def aliasIdx = idx + 1
                    joinClause  += " POSITIONALLY JOIN read_parquet('${SqlUtils.escapePath(fp)}') AS t${aliasIdx}"
                    selectClause += ", t${aliasIdx}.* EXCLUDE (timestamp)"
                }

                def query = "COPY (SELECT ${selectClause} FROM ${joinClause}) TO '${SqlUtils.escapePath(localDest.absolutePath)}' (FORMAT PARQUET, COMPRESSION 'ZSTD')"
                SqlUtils.executeQuery(conn, query)
            }

            def outputDest = new java.io.File(targetBinDir, targetFile)
            java.nio.file.Files.copy(localDest.toPath(), outputDest.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)
            BaseFileSystemUtils.appendLog(participantLog, "[L1] [INFO] [NATIVE_JOIN] Successfully created joined matrix: ${targetFile}")

        } catch (Throwable t) {
            def fatalErr = "[NATIVE_JOIN] CRITICAL ERROR for participant '${currentId}': ${t.message}"
            if (participantLog != null && BaseFileSystemUtils != null) {
                BaseFileSystemUtils.appendLog(participantLog, "[L1] [ERROR] ${fatalErr}")
            }
            throw new RuntimeException(fatalErr, t)
        }
}