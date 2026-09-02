nextflow.enable.dsl=2

process NATIVE_CONCAT {

    publishDir (
        path: {
            def isL2 = (level_tag ? level_tag.toString().trim().toUpperCase() : "L1") == "L2"
            def l2FolderVal = params.l2_folder ? params.l2_folder.toString().trim() : "${params.project_name}_l2"
            return isL2 ? 
                "${params.output_dir}/${l2FolderVal}/.bin" : 
                "${params.output_dir}/${params.project_name}_l1/${participant_id}/.bin"
        },
        mode: 'copy',
        pattern: "*.parquet"
    )

    input:
        val parquet_files
        val name_appendage
        val level_tag

    output:
        path("*.parquet"), emit: signal

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
            throw new java.io.FileNotFoundException("[NATIVE_CONCAT] CRITICAL: Required utility classes missing from classpath.")
        }

        gcl.addClasspath(managerFile.parentFile.absolutePath)
        def TM                  = gcl.parseClass(managerFile)
        def BaseFileSystemUtils = gcl.parseClass(baseFsFile)

        def outputDirVal   = params.output_dir.toString().trim()
        def projectNameVal = params.project_name.toString().trim()
        def l2FolderVal    = params.l2_folder ? params.l2_folder.toString().trim() : "${projectNameVal}_l2"
        def launchDir      = workflow.launchDir.toFile()
        
        def lvl            = level_tag ? level_tag.toString().trim().toUpperCase() : "L1"
        def isL2           = (lvl == "L2")
        def tag            = name_appendage ? name_appendage.toString().trim().toUpperCase() : "CONCAT"

        // Identifier derivation: L2 targets project cohort name; L1 derives participant ID
        def derivedId  = isL2 ? projectNameVal : TM.deriveIdentifier(parquet_files)
        participant_id = derivedId ?: "UNKNOWN_IDENTIFIER"

        // Context folder & log path branching based on level_tag
        def contextFolderName = isL2 ? l2FolderVal : "${projectNameVal}_l1/${participant_id}"
        def logFileName       = isL2 ? "${projectNameVal}_l2.log" : "${participant_id}.log"

        def realLogDir = new java.io.File(launchDir, "${outputDirVal}/${contextFolderName}/.bin")
        if (!realLogDir.exists()) realLogDir.mkdirs()
        File mainLog = new java.io.File(realLogDir, logFileName)

        if (!isL2 && derivedId == null) {
            BaseFileSystemUtils.appendLog(mainLog, "[${lvl}] [ERROR] [NATIVE_CONCAT] [${tag}] Probe failed: unable to derive participant identifier.")
            throw new IllegalStateException("[NATIVE_CONCAT] Probe failed: Identifier derivation failed.")
        }

        BaseFileSystemUtils.appendLog(mainLog, "[${lvl}] [INFO] [NATIVE_CONCAT] [${tag}] Probe succeeded: target ID '${participant_id}'.")
        BaseFileSystemUtils.appendLog(mainLog, "[${lvl}] [INFO] [NATIVE_CONCAT] [${tag}] === Target ${participant_id} Concat Initialized (${lvl}) ===")

        BaseFileSystemUtils.appendLog(mainLog, "[${lvl}] [INFO] [NATIVE_CONCAT] [${tag}] Step 1/2: Probing input signals validation...")
        
        List<String> cleanFiles = TM.validateInputs(parquet_files)
        if (cleanFiles != null) {
            BaseFileSystemUtils.appendLog(mainLog, "[${lvl}] [INFO] [NATIVE_CONCAT] [${tag}] Step 1/2 Complete: Validation probe succeeded (${cleanFiles.size()} files validated).")
        } else {
            BaseFileSystemUtils.appendLog(mainLog, "[${lvl}] [ERROR] [NATIVE_CONCAT] [${tag}] Step 1/2 Failed: Validation probe reported invalid or missing inputs.")
            throw new IllegalStateException("[NATIVE_CONCAT] Step 1 Failed: Input validation probe failed.")
        }

        BaseFileSystemUtils.appendLog(mainLog, "[${lvl}] [INFO] [NATIVE_CONCAT] [${tag}] Step 2/2: Probing DuckDB vertical concat operation...")
        
        def outputFileName = "${participant_id}_${name_appendage}_concat.parquet"
        def localDest      = new java.io.File(task.workDir.toFile(), outputFileName)

        String execError = TM.executeConcat(cleanFiles, localDest.absolutePath, false)
        if (execError == null) {
            BaseFileSystemUtils.appendLog(mainLog, "[${lvl}] [INFO] [NATIVE_CONCAT] [${tag}] Step 2/2 Complete: Execution probe succeeded (concat matrix generated).")
        } else {
            BaseFileSystemUtils.appendLog(mainLog, "[${lvl}] [ERROR] [NATIVE_CONCAT] [${tag}] Step 2/2 Failed: ${execError}")
            throw new IllegalStateException("[NATIVE_CONCAT] Step 2 Failed: ${execError}")
        }
}