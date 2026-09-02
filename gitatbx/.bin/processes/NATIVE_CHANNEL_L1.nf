nextflow.enable.dsl=2

process NATIVE_CHANNEL_L1 {
    executor 'local'
    
    input:
        tuple val(id), val(raw_input_folder), val(l1_folder)
        val file_pattern        
        val plot_type       

    output:
        path("${id}_*.parquet"), emit: signal

    exec:
        // Fail-fast input validation
        if (!id || id.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_CHANNEL] FATAL: Participant 'id' is required and cannot be null/empty.")
        }
        if (!raw_input_folder || raw_input_folder.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_CHANNEL] FATAL: 'raw_input_folder' is required for participant '${id}'.")
        }
        if (!l1_folder || l1_folder.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_CHANNEL] FATAL: 'l1_folder' is required for participant '${id}'.")
        }
        if (!file_pattern || file_pattern.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_CHANNEL] FATAL: 'file_pattern' must be explicitly declared.")
        }

        def currentId  = id.toString().trim()
        def rawPattern = file_pattern.toString().trim()
        def plotType   = plot_type ? plot_type.toString().trim() : ""
        def l1Folder   = l1_folder.toString().trim()
        def rawInpDir  = raw_input_folder.toString().trim()

        def launchDir = workflow.launchDir.toFile()

        if (!params.output_dir || !params.project_name) {
            throw new IllegalStateException("[NATIVE_CHANNEL] FATAL: Missing 'params.output_dir' or 'params.project_name'.")
        }

        def baseL1 = l1Folder.isEmpty() ? 
            new java.io.File(launchDir, "${params.output_dir}/${params.project_name}_l1") :
            (l1Folder.startsWith('/') ? new java.io.File(l1Folder) : new java.io.File(launchDir, l1Folder))

        def participantDir = (baseL1.name == currentId) ? 
            baseL1.getCanonicalFile() : 
            new java.io.File(baseL1, currentId).getCanonicalFile()

        def targetBinDir = new java.io.File(participantDir, ".bin")

        participantDir.mkdirs()
        targetBinDir.mkdirs()

        def participantLog = new java.io.File(targetBinDir, "${currentId}.log")

        def SqlUtils            = null
        def BaseFileSystemUtils = null

        try {
            def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)

            def jarDirs = [
                moduleDir.resolve('../lib/jars').toFile(),
                moduleDir.resolve('../../lib/jars').toFile(),
                moduleDir.resolve('../lib').toFile(),
                moduleDir.resolve('../../lib').toFile()
            ]
            
            jarDirs.each { java.io.File jarDir ->
                if (jarDir.exists() && jarDir.isDirectory()) {
                    jarDir.eachFileMatch(~/(?i).*\.jar$/) { java.io.File jarFile ->
                        gcl.addURL(jarFile.toURI().toURL())
                    }
                }
            }

            def sqlUtilsFile = [
                moduleDir.resolve('../lib/SqlUtils.groovy').toFile(),
                moduleDir.resolve('../../lib/SqlUtils.groovy').toFile()
            ].find { java.io.File candidateFile -> candidateFile.exists() }

            def baseFsFile = [
                moduleDir.resolve('../lib/base/BaseFileSystemUtils.groovy').toFile(),
                moduleDir.resolve('../../lib/base/BaseFileSystemUtils.groovy').toFile()
            ].find { java.io.File candidateFile -> candidateFile.exists() }

            if (!sqlUtilsFile) throw new java.io.FileNotFoundException("[NATIVE_CHANNEL] Missing SqlUtils.groovy in lib tree.")
            if (!baseFsFile)  throw new java.io.FileNotFoundException("[NATIVE_CHANNEL] Missing BaseFileSystemUtils.groovy in lib tree.")

            if (sqlUtilsFile.parentFile.exists()) {
                gcl.addClasspath(sqlUtilsFile.parentFile.absolutePath)
                if (sqlUtilsFile.parentFile.parentFile.exists()) {
                    gcl.addClasspath(sqlUtilsFile.parentFile.parentFile.absolutePath)
                }
            }

            SqlUtils            = gcl.parseClass(sqlUtilsFile)
            BaseFileSystemUtils = gcl.parseClass(baseFsFile)

            if (!participantLog.exists() || participantLog.length() == 0) {
                BaseFileSystemUtils.appendLog(participantLog, "=== Participant ${currentId} L1 Processing Initialized ===")
            }
            BaseFileSystemUtils.appendLog(participantLog, "[L1] [INFO] [NATIVE_CHANNEL] Staging channel for '${currentId}' with pattern '${rawPattern}'...")

            def cleanTag = rawPattern.replaceAll(/[*"]/, '')
                                     .replaceAll(/^(\._|_)/, '')
                                     .replaceAll(/(?i)\.(parquet|fif|csv|tsv)$/, '')
                                     .replaceAll(/(?i)_channel$/, '')
            def canonicalName = "${currentId}_${cleanTag}.parquet"

            def participantSrcDir = new java.io.File(rawInpDir).getCanonicalFile()
            if (!participantSrcDir.exists() || !participantSrcDir.isDirectory()) {
                def err = "[NATIVE_CHANNEL] ERROR: Discovered raw input directory missing for '${currentId}' at: '${rawInpDir}'"
                BaseFileSystemUtils.appendLog(participantLog, "[L1] [ERROR] ${err}")
                throw new java.io.FileNotFoundException(err)
            }

            def candidateFiles = []
            participantSrcDir.eachFileRecurse { java.io.File discoveredFile -> 
                if (discoveredFile.isFile() && !discoveredFile.name.startsWith('.')) {
                    candidateFiles.add(discoveredFile) 
                }
            }

            def tagLower = cleanTag.toLowerCase()
            def searchKeywords = [tagLower]
            if (tagLower == 'sam') searchKeywords += ['questionnaire', 'survey', 'self_report', 'ratings', 'valence', 'arousal']
            if (tagLower == 'eeg') searchKeywords += ['spectrum', 'eeg_epochs', 'raw_eeg']
            if (tagLower == 'eda') searchKeywords += ['gsr', 'amplitude', 'skin']
            if (tagLower == 'ecg' || tagLower == 'hrv') searchKeywords += ['ecg', 'hrv', 'peaks', 'interval', 'cardio']

            def matchingFiles = candidateFiles.findAll { java.io.File fileCandidate ->
                def nameLower = fileCandidate.name.toLowerCase()
                boolean validExt = nameLower.endsWith('.parquet') || nameLower.endsWith('.csv') || nameLower.endsWith('.tsv')
                if (!validExt) return false
                return searchKeywords.any { String kw -> nameLower.contains(kw) }
            }

            if (matchingFiles.isEmpty()) {
                def availNames = candidateFiles.collect { java.io.File fileItem -> fileItem.name }
                def err = "[NATIVE_CHANNEL] ERROR: Pattern '${cleanTag}' (keywords: ${searchKeywords}) not found in '${participantSrcDir.absolutePath}'. Available files: ${availNames}"
                BaseFileSystemUtils.appendLog(participantLog, "[L1] [ERROR] ${err}")
                throw new java.io.FileNotFoundException(err)
            }

            if (matchingFiles.size() > 1) {
                // FIXED: Explicit parameter declaration 'matchedFile ->' instead of implicit 'it'
                def matchedPaths = matchingFiles.collect { java.io.File matchedFile -> matchedFile.absolutePath }
                def err = "[NATIVE_CHANNEL] ERROR: Ambiguous match for tag '${cleanTag}'. Multiple candidate files found: ${matchedPaths}"
                BaseFileSystemUtils.appendLog(participantLog, "[L1] [ERROR] ${err}")
                throw new IllegalStateException(err)
            }

            def srcFile = matchingFiles.first()
            BaseFileSystemUtils.appendLog(participantLog, "[L1] [INFO] [NATIVE_CHANNEL] Matched source file: ${srcFile.absolutePath}")

            def localDest  = new java.io.File(task.workDir.toFile().getCanonicalFile(), canonicalName)
            def outputDest = new java.io.File(targetBinDir, canonicalName)

            try {
                def escSrc  = SqlUtils.escapeSql(srcFile.absolutePath)
                def escLoc  = SqlUtils.escapeSql(localDest.absolutePath)
                def escType = SqlUtils.escapeSql(plotType)

                def readQuery = srcFile.name.toLowerCase().endsWith('.parquet') ? 
                    "read_parquet('${escSrc}')" : 
                    "read_csv_auto('${escSrc}')"

                def selectProjection = "SELECT *"
                if (tagLower == 'ecg') {
                    selectProjection = "SELECT * EXCLUDE (ecg), ecg AS ECG"
                } else if (tagLower == 'eda') {
                    selectProjection = "SELECT * EXCLUDE (eda), eda AS EDA"
                }

                def queryLocal = """
                    COPY (
                        ${selectProjection} FROM ${readQuery}
                    ) TO '${escLoc}' (FORMAT PARQUET, COMPRESSION 'ZSTD', KV_METADATA {'plot_type': '${escType}'})
                """

                SqlUtils.executeQuery(queryLocal)
                BaseFileSystemUtils.appendLog(participantLog, "[L1] [INFO] [NATIVE_CHANNEL] Staged '${canonicalName}' locally via DuckDB (ZSTD).")
            } catch (Throwable duckDbErr) {
                Throwable rootCause = (SqlUtils != null && SqlUtils.respondsTo('getRootCause')) ? SqlUtils.getRootCause(duckDbErr) : (duckDbErr.cause ?: duckDbErr)
                def fatalMsg = "[NATIVE_CHANNEL] FAIL-FAST: DuckDB SQL staging failed for '${currentId}' (${rootCause.getClass().getName()}: ${rootCause.getMessage()})."
                if (BaseFileSystemUtils != null) BaseFileSystemUtils.appendLog(participantLog, "[L1] [FATAL] ${fatalMsg}")
                throw new RuntimeException(fatalMsg, duckDbErr)
            }

            java.nio.file.Files.copy(localDest.toPath(), outputDest.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)
            BaseFileSystemUtils.appendLog(participantLog, "[L1] [INFO] [NATIVE_CHANNEL] Successfully published '${canonicalName}' to: ${outputDest.absolutePath}")

        } catch (Throwable t) {
            def fatalErr = "[NATIVE_CHANNEL] CRITICAL ERROR for participant '${currentId}': ${t.message}"
            if (participantLog != null && participantLog.exists() && BaseFileSystemUtils != null) {
                BaseFileSystemUtils.appendLog(participantLog, "[L1] [ERROR] ${fatalErr}")
            }
            throw new RuntimeException(fatalErr, t)
        }
}