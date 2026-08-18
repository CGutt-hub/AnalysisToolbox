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
        tuple val(signal_suffix), path("*.parquet"), emit: cohort_matrix
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

        // Handle both single String/Path and List/Collection natively
        def rawPath    = (participant_path instanceof List || participant_path.getClass().isArray()) ? participant_path.first() : participant_path
        def sampleFile = new java.io.File(rawPath.toString().trim())
        if (!sampleFile.isAbsolute()) {
            sampleFile = new java.io.File(launchDir, rawPath.toString().trim())
        }

        def parentDir = sampleFile.parentFile
        if (!parentDir || !parentDir.exists()) {
            def errMsg = "Step 1 Failed: Parent directory '${parentDir}' does not exist for participant path '${rawPath}'."
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [NATIVE_CHANNEL_L2] [${tag}] ${errMsg}")
            throw new IllegalStateException("[NATIVE_CHANNEL_L2] ${errMsg}")
        }

        def sampleName    = sampleFile.name
        def prefixMatcher = sampleName =~ /^([a-zA-Z0-9]+)_/
        def prefix        = prefixMatcher.find() ? prefixMatcher.group(1) : sampleName.replaceAll(/_\d+.*$/, '')

        List<java.io.File> participantDirs = parentDir.listFiles({ java.io.File dir, String name ->
            new java.io.File(dir, name).isDirectory() && (name.startsWith("${prefix}_") || name == sampleName)
        } as java.io.FilenameFilter) as List<java.io.File>

        if (!participantDirs || participantDirs.isEmpty()) {
            def errMsg = "Step 1 Failed: No participant directories matching pattern '${prefix}_*' found in '${parentDir.absolutePath}'."
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [NATIVE_CHANNEL_L2] [${tag}] ${errMsg}")
            throw new IllegalStateException("[NATIVE_CHANNEL_L2] ${errMsg}")
        }

        List<String> participantPathStrings = participantDirs.collect { java.io.File pDir -> pDir.absolutePath }
        List<String> resolvedFiles          = TM.resolveCohortSignalFiles(participantPathStrings, signal_suffix, launchDir)
        List<String> cleanFiles             = TM.validateInputs(resolvedFiles)

        if (cleanFiles != null && !cleanFiles.isEmpty()) {
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [INFO] [NATIVE_CHANNEL_L2] [${tag}] Step 1/2 Complete: Validation probe succeeded (${cleanFiles.size()} files resolved and validated).")
        } else {
            def errMsg = "Step 1 Failed: Validation probe reported missing or invalid participant files for signal pattern '${signal_suffix}'."
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [NATIVE_CHANNEL_L2] [${tag}] ${errMsg}")
            throw new IllegalStateException("[NATIVE_CHANNEL_L2] ${errMsg}")
        }

        // STEP 2: Execute Cohort Concatenation
        BaseFileSystemUtils.appendLog(mainLog, "[L2] [INFO] [NATIVE_CHANNEL_L2] [${tag}] Step 2/2: Probing DuckDB cohort concatenation...")

        def outputFileName = "${projectNameVal}_binned_${signal_suffix}.parquet"
        def localDest      = new java.io.File(task.workDir.toFile(), outputFileName)

        String execError = TM.executeCohortConcatenation(cleanFiles, localDest.absolutePath, "participant_id")

        if (execError == null) {
            def cohortBinDest = new java.io.File(l2BinDir, outputFileName)
            java.nio.file.Files.copy(localDest.toPath(), cohortBinDest.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)

            BaseFileSystemUtils.appendLog(mainLog, "[L2] [INFO] [NATIVE_CHANNEL_L2] [${tag}] Step 2/2 Complete: Execution probe succeeded (binned cohort matrix generated).")
        } else {
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [NATIVE_CHANNEL_L2] [${tag}] Step 2/2 Failed: ${execError}")
            throw new IllegalStateException("[NATIVE_CHANNEL_L2] Step 2 Failed: ${execError}")
        }
}