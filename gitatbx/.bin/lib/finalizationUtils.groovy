class FinalizationUtils {

    static void finalizeFiles(
            Object workflowObj, 
            Map paramsObj, 
            java.nio.file.Path moduleDirFile, 
            String entityName, 
            String targetDirPath, 
            Object rootSignals, 
            Object syncTriggers, 
            String logPrefix, 
            String commitMessage) {

        def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
        gcl.addClasspath(moduleDirFile.resolve('../').toFile().absolutePath)

        def BaseGitUtils        = gcl.parseClass(moduleDirFile.resolve('../lib/base/BaseGitUtils.groovy').toFile())
        def BaseFileSystemUtils = gcl.parseClass(moduleDirFile.resolve('../lib/base/BaseFileSystemUtils.groovy').toFile())

        def rootDir   = new java.io.File("${workflowObj.launchDir}/${targetDirPath}")
        def binDir    = new java.io.File(rootDir, ".bin")
        def entityLog = new java.io.File(binDir, entityName ? "${entityName}.log" : "${paramsObj.project_name}_l2.log")
        def mainLog   = new java.io.File("${workflowObj.launchDir}/${paramsObj.output_dir}/.bin", "${paramsObj.project_name}.log")

        if (!rootDir.exists() && !rootDir.mkdirs()) {
            throw new RuntimeException("CRITICAL: Failed to create root directory: ${rootDir.absolutePath}")
        }
        if (!binDir.exists() && !binDir.mkdirs()) {
            throw new RuntimeException("CRITICAL: Failed to create .bin directory: ${binDir.absolutePath}")
        }

        BaseFileSystemUtils.appendLog(entityLog, "[${logPrefix}] [INFO] Starting finalization for ${entityName ?: 'L2 Cohort'}...")

        def rootSignalsList  = rootSignals instanceof List ? rootSignals : [rootSignals]
        def syncTriggersList = syncTriggers instanceof List ? syncTriggers : [syncTriggers]

        def cleanRootFiles = rootSignalsList.collect { it.toString() }.findAll { fp ->
            def f = new java.io.File(fp)
            return f.exists() && f.size() > 12
        }
        def cleanSyncFiles = syncTriggersList.collect { it.toString() }.findAll { fp ->
            def f = new java.io.File(fp)
            return f.exists() && f.size() > 12
        }

        if (cleanRootFiles.isEmpty())  throw new RuntimeException("CRITICAL: Zero result signal files for ${entityName ?: 'L2'}.")
        if (cleanSyncFiles.isEmpty()) throw new RuntimeException("CRITICAL: Zero sync trigger files for ${entityName ?: 'L2'}.")

        try {
            cleanRootFiles.each { signal ->
                def srcFile = new java.io.File(signal)
                def destFile = new java.io.File(rootDir, srcFile.name)
                java.nio.file.Files.copy(srcFile.toPath(), destFile.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)
            }

            cleanSyncFiles.each { trigger ->
                def srcFile = new java.io.File(trigger)
                def destFile = new java.io.File(rootDir, srcFile.name)
                java.nio.file.Files.copy(srcFile.toPath(), destFile.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)
            }

            def gitRoot = BaseGitUtils.findGitRoot(new java.io.File(workflowObj.launchDir.toString()))
            if (!gitRoot) throw new RuntimeException("CRITICAL: Git repository root not found for ${entityName ?: 'L2'}.")

            BaseGitUtils.syncPath(gitRoot, targetDirPath, commitMessage)

            BaseFileSystemUtils.appendLog(entityLog, "=== Finalization Complete ===")
            BaseFileSystemUtils.appendLog(mainLog, "[${logPrefix.toLowerCase()}] Git sync completed for ${entityName ?: 'L2'}")

        } catch (Exception e) {
            BaseFileSystemUtils.appendLog(entityLog, "[${logPrefix}] [ERROR] Finalization failed: ${e.message}")
            throw e
        }
    }
}