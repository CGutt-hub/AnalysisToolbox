nextflow.enable.dsl=2

process NATIVE_CHANNEL_L2 {

    maxForks 1

    publishDir (
        path: { "${params.output_dir}/${params.l2_folder}/.bin" },
        mode: 'copy',
        pattern: "*.parquet"
    )

    input:
        val participant_path
        val target_signal_name

    output:
        path("*.parquet"), emit: signal
        path("*.parquet"), emit: cohort_file

    exec:
        def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)

        def managerFile = [
            moduleDir.resolve('../lib/TableManager.groovy').toFile(),
            moduleDir.resolve('../../lib/TableManager.groovy').toFile()
        ].find { java.io.File candidateFile -> candidateFile && candidateFile.exists() }

        def baseFsFile = [
            moduleDir.resolve('../lib/base/BaseFileSystemUtils.groovy').toFile(),
            moduleDir.resolve('../../lib/base/BaseFileSystemUtils.groovy').toFile()
        ].find { java.io.File candidateFile -> candidateFile && candidateFile.exists() }

        if (!managerFile || !baseFsFile) {
            throw new java.io.FileNotFoundException("[NATIVE_CHANNEL_L2] CRITICAL: Required utility classes missing from classpath.")
        }

        gcl.addClasspath(managerFile.parentFile.absolutePath)
        def TM                  = gcl.parseClass(managerFile)
        def BaseFileSystemUtils = gcl.parseClass(baseFsFile)

        def outputDirVal   = params.output_dir.toString().trim()
        def l2FolderVal    = params.l2_folder ? params.l2_folder.toString().trim() : "${params.project_name}_l2"
        def projectNameVal = params.project_name.toString().trim()
        def launchDir      = workflow.launchDir.toFile()
        signal_suffix      = target_signal_name.toString().trim()
        def tag            = signal_suffix.toUpperCase()

        def l2BinDir = new java.io.File(launchDir, "${outputDirVal}/${l2FolderVal}/.bin")
        if (!l2BinDir.exists() && !l2BinDir.mkdirs()) {
            throw new java.io.IOException("[NATIVE_CHANNEL_L2] CRITICAL: Failed to create bin directory at '${l2BinDir.absolutePath}'.")
        }

        File mainLog = new java.io.File(l2BinDir, "${projectNameVal}_l2.log")

        BaseFileSystemUtils.appendLog(mainLog, "[L2] [INFO] [NATIVE_CHANNEL_L2] [${tag}] === Cohort Channel ${signal_suffix} Initialized ===")

        def rawPath   = (participant_path instanceof List || participant_path.getClass().isArray()) ? participant_path.first() : participant_path
        def parentDir = new java.io.File(rawPath.toString().trim())
        if (!parentDir.isAbsolute()) {
            parentDir = new java.io.File(launchDir, rawPath.toString().trim())
        }

        if (!parentDir.exists()) {
            def errMsg = "Step 1 Failed: Parent L1 directory '${parentDir.absolutePath}' does not exist."
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [NATIVE_CHANNEL_L2] [${tag}] ${errMsg}")
            throw new IllegalStateException("[NATIVE_CHANNEL_L2] ${errMsg}")
        }

        List<java.io.File> participantDirs = parentDir.listFiles({ dir, name ->
            new java.io.File(dir, name).isDirectory() && name.startsWith("${projectNameVal}_")
        } as java.io.FilenameFilter) as List<java.io.File>

        if (!participantDirs || participantDirs.isEmpty()) {
            def errMsg = "Step 1 Failed: No participant directories matching pattern '${projectNameVal}_*' found in '${parentDir.absolutePath}'."
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [NATIVE_CHANNEL_L2] [${tag}] ${errMsg}")
            throw new IllegalStateException("[NATIVE_CHANNEL_L2] ${errMsg}")
        }

        List<String> resolvedFiles = []
        participantDirs.each { pDir ->
            def binSubDir = new java.io.File(pDir, ".bin")
            if (binSubDir.exists() && binSubDir.isDirectory()) {
                def matchingFiles = binSubDir.listFiles({ _dir, name ->
                    name.contains(signal_suffix) && name.endsWith(".parquet")
                } as java.io.FilenameFilter)

                if (matchingFiles != null && matchingFiles.length > 0) {
                    resolvedFiles.add(matchingFiles[0].absolutePath)
                }
            }
        }

        List<String> cleanFiles = TM.validateInputs(resolvedFiles)

        if (cleanFiles != null && !cleanFiles.isEmpty()) {
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [INFO] [NATIVE_CHANNEL_L2] [${tag}] Step 1/2 Complete: Validation probe succeeded (${cleanFiles.size()} files resolved and validated).")
        } else {
            def errMsg = "Step 1 Failed: Validation probe reported missing or invalid participant files for signal pattern '*${signal_suffix}'."
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [NATIVE_CHANNEL_L2] [${tag}] ${errMsg}")
            throw new IllegalStateException("[NATIVE_CHANNEL_L2] ${errMsg}")
        }

        BaseFileSystemUtils.appendLog(mainLog, "[L2] [INFO] [NATIVE_CHANNEL_L2] [${tag}] Step 2/2: Probing DuckDB cohort concatenation...")

        def outputFileName = "${projectNameVal}_binned_${signal_suffix}.parquet"
        def localDest      = new java.io.File(task.workDir.toFile(), outputFileName)

        String execError = TM.executeCohortConcatenation(cleanFiles, localDest.absolutePath, "id")

        if (execError == null) {
            def cohortBinDest = new java.io.File(l2BinDir, outputFileName)
            java.nio.file.Files.copy(localDest.toPath(), cohortBinDest.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)

            BaseFileSystemUtils.appendLog(mainLog, "[L2] [INFO] [NATIVE_CHANNEL_L2] [${tag}] Step 2/2 Complete: Execution probe succeeded (binned cohort matrix generated).")
        } else {
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [NATIVE_CHANNEL_L2] [${tag}] Step 2/2 Failed: ${execError}")
            throw new IllegalStateException("[NATIVE_CHANNEL_L2] Step 2 Failed: ${execError}")
        }
}