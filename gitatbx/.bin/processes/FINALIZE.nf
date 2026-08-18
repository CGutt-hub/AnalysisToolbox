nextflow.enable.dsl=2

process FINALIZE {

    input:
        val root_signals
        val sync_triggers
        val level

    output:
        val(
            level.toString().trim().toUpperCase() == "L1"
                ? "${params.output_dir}/${params.project_name}_l1/${(root_signals[0] instanceof List ? root_signals[0][-1] : root_signals[0]).toString().with { fp -> def fn = new java.io.File(fp).name; def p = params.project_name + '_'; fn.startsWith(p) ? p + fn.substring(p.length()).split('[_\\.-]')[0] : fn.split('[_\\.-]')[0] }}"
                : true
        ), emit: finalized_path

    exec:
        def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)

        def fuFile = [
            moduleDir.resolve('../lib/FinalizationUtils.groovy').toFile(),
            moduleDir.resolve('../../lib/FinalizationUtils.groovy').toFile()
        ].find { java.io.File candidateFile -> candidateFile && candidateFile.exists() }

        def baseGitFile = [
            moduleDir.resolve('../lib/base/BaseGitUtils.groovy').toFile(),
            moduleDir.resolve('../../lib/base/BaseGitUtils.groovy').toFile()
        ].find { java.io.File candidateFile -> candidateFile && candidateFile.exists() }

        def baseFsFile = [
            moduleDir.resolve('../lib/base/BaseFileSystemUtils.groovy').toFile(),
            moduleDir.resolve('../../lib/base/BaseFileSystemUtils.groovy').toFile()
        ].find { java.io.File candidateFile -> candidateFile && candidateFile.exists() }

        if (!fuFile || !baseGitFile || !baseFsFile) {
            throw new java.io.FileNotFoundException("[FINALIZE] CRITICAL: Required utility classes missing from classpath.")
        }

        gcl.addClasspath(fuFile.parentFile.absolutePath)
        def FU                  = gcl.parseClass(fuFile)
        def BaseGitUtils        = gcl.parseClass(baseGitFile)
        def BaseFileSystemUtils = gcl.parseClass(baseFsFile)

        def lvlUpper   = level.toString().trim().toUpperCase()
        def processTag = "FINALIZE_${lvlUpper}"
        def lvl        = lvlUpper

        def outputDirVal   = params.output_dir.toString().trim()
        def projectNameVal = params.project_name.toString().trim()
        def launchDir      = workflow.launchDir.toFile()

        def targetRelPath  = ""
        def pidStr         = ""
        def logFileName    = ""

        if (lvlUpper == "L1") {
            def samplePath = (root_signals[0] instanceof List ? root_signals[0][-1] : root_signals[0]).toString()
            def sampleName = new java.io.File(samplePath).name
            def prefix     = "${projectNameVal}_"
            pidStr         = sampleName.startsWith(prefix) ? 
                             prefix + sampleName.substring(prefix.length()).split("[_\\.-]")[0] : 
                             sampleName.split("[_\\.-]")[0]

            targetRelPath = "${outputDirVal}/${projectNameVal}_l1/${pidStr}"
            logFileName   = "${pidStr}.log"
        } else if (lvlUpper == "L2") {
            pidStr        = "L2 Cohort"
            targetRelPath = "${outputDirVal}/${projectNameVal}_l2"
            logFileName   = "${projectNameVal}_l2.log"
        } else {
            throw new IllegalArgumentException("[${processTag}] Invalid level parameter provided: '${level}'. Must be 'L1' or 'L2'.")
        }

        def targetRootDir = new java.io.File(launchDir, targetRelPath)
        def binDir        = new java.io.File(targetRootDir, ".bin")
        if (!binDir.exists() && !binDir.mkdirs()) {
            throw new IllegalStateException("[${processTag}] Failed to create directory structure: ${binDir.absolutePath}")
        }

        File targetLogFile = new java.io.File(binDir, logFileName)
        File globalLog     = new java.io.File(launchDir, "${outputDirVal}/.bin/${projectNameVal}.log")

        if (lvlUpper == "L1") {
            BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] Derived participant ID '${pidStr}' directly from input path.")
            BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] === Participant ${pidStr} Finalization Initialized ===")
        } else {
            BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] Probe succeeded: cohort target directory initialized.")
            BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] === Cohort L2 Finalization Initialized ===")
        }

        // STEP 1: Probe validation of incoming signals
        BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] Step 1/3: Probing input signals validation...")
        def cleanRoot = FU.validateSignals(root_signals, 12L)
        def cleanSync = FU.validateSignals(sync_triggers, 12L)

        if (cleanRoot.isEmpty() || cleanSync.isEmpty()) {
            BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [ERROR] [${processTag}] [FINALIZATION] Step 1/3 Failed: Validation probe reported missing or invalid signal/trigger files.")
            throw new IllegalStateException("[${processTag}] Step 1 Failed: Signal validation probe failed for ${pidStr}.")
        }
        BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] Step 1/3 Complete: Validation probe succeeded (${cleanRoot.size()} root signals validated).")

        // STEP 2: Promote root signals
        BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] Step 2/3: Probing file promotion...")
        def promoteRes = FU.promoteSignals(cleanRoot, targetRootDir)
        if (!promoteRes.success) {
            BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [ERROR] [${processTag}] [FINALIZATION] Step 2/3 Failed: ${promoteRes.error}")
            throw new IllegalStateException("[${processTag}] Step 2 Failed: File promotion failed for ${pidStr}. Reason: ${promoteRes.error}")
        }

        def promotedFiles = promoteRes.promotedFiles
        promotedFiles.each { fname ->
            BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] Promoted signal file: ${fname}")
        }
        BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] Step 2/3 Complete: Promotion probe succeeded (${promotedFiles.size()} files promoted).")

        // Remove lock file
        if (FU.removeLockFile(binDir)) {
            BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] Gracefully removed log lock file for ${pidStr}")
        }

        // STEP 3: Git Sync Execution
        BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] Step 3/3: Handing off to Git auto-sync operation...")
        if (lvlUpper == "L1") {
            BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] === Participant ${pidStr} Finalization Complete ===")
        } else {
            BaseFileSystemUtils.appendLog(targetLogFile, "[${lvl}] [INFO] [${processTag}] [FINALIZATION] === Cohort L2 Finalization Complete ===")
        }

        def gitRoot = FU.findGitRoot(BaseGitUtils, launchDir)
        if (gitRoot != null) {
            def commitMsg = (lvlUpper == "L1") ? 
                            "autosync: finalize participant ${pidStr} L1 outputs" : 
                            "autosync: finalize cohort ${projectNameVal} L2 outputs"
                            
            def gitRes = FU.executeGitSync(BaseGitUtils, gitRoot, targetRelPath, commitMsg)
            if (gitRes.success) {
                BaseFileSystemUtils.appendLog(globalLog, "[${lvl}] [INFO] [${processTag}] Git sync completed for ${pidStr}")
            } else {
                BaseFileSystemUtils.appendLog(globalLog, "[${lvl}] [WARN] [${processTag}] Git sync failed for ${pidStr} (Non-fatal): ${gitRes.error}")
            }
        } else {
            BaseFileSystemUtils.appendLog(globalLog, "[${lvl}] [INFO] [${processTag}] Git repository root not found. Skipping optional git auto-sync for ${pidStr}")
        }
}