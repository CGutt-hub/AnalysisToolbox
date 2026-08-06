process NATIVE_SPLIT {
    executor 'local'
    
    input:
        tuple val(id), path(context_path)
        val full_pattern

    output:
        tuple val(id), path("*.{fif,parquet}"), emit: isolated_file

    exec:
        if (!id || id.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_SPLIT] FATAL: Participant 'id' is required.")
        }
        if (!full_pattern || full_pattern.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_SPLIT] FATAL: 'full_pattern' is required.")
        }

        def currentId  = id.toString().trim()
        def patternStr = full_pattern.toString().trim()
        def contextFile = context_path ? context_path.toFile() : null
        def launchDir  = workflow.launchDir.toFile()

        if (!params.output_dir || !params.project_name) {
            throw new IllegalStateException("[NATIVE_SPLIT] FATAL: Missing 'params.output_dir' or 'params.project_name'.")
        }

        def participantDir = new java.io.File(launchDir, "${params.output_dir}/${params.project_name}_l1/${currentId}")
        def targetBinDir   = new java.io.File(participantDir, ".bin")
        participantDir.mkdirs()
        targetBinDir.mkdirs()

        def participantLog = new java.io.File(targetBinDir, "${currentId}.log")
        def BaseFileSystemUtils = null

        try {
            def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
            def baseFsFile = [
                moduleDir.resolve('../lib/base/BaseFileSystemUtils.groovy').toFile(),
                moduleDir.resolve('../../lib/base/BaseFileSystemUtils.groovy').toFile()
            ].find { java.io.File f -> f.exists() }

            if (!baseFsFile) throw new java.io.FileNotFoundException("[NATIVE_SPLIT] Missing BaseFileSystemUtils.groovy.")

            gcl.addClasspath(baseFsFile.parentFile.absolutePath)
            if (baseFsFile.parentFile.parentFile.exists()) {
                gcl.addClasspath(baseFsFile.parentFile.parentFile.absolutePath)
            }

            BaseFileSystemUtils = gcl.parseClass(baseFsFile)
            BaseFileSystemUtils.appendLog(participantLog, "[L1] [INFO] [NATIVE_SPLIT] Extracting pattern '${patternStr}' for '${currentId}'...")

            def srcFile = null
            if (contextFile && contextFile.isFile()) {
                srcFile = contextFile
            } else if (contextFile && contextFile.isDirectory()) {
                def regexPattern = patternStr.replace('.', '\\.').replace('*', '.*')
                def matchedFiles = contextFile.listFiles().findAll { java.io.File file ->
                    file.isFile() && file.name ==~ regexPattern
                }
                if (!matchedFiles.isEmpty()) {
                    srcFile = matchedFiles[0]
                }
            }

            if (!srcFile || !srcFile.exists()) {
                def err = "[NATIVE_SPLIT] ERROR: Missing file for pattern '${patternStr}' in ${context_path}!"
                BaseFileSystemUtils.appendLog(participantLog, "[L1] [ERROR] ${err}")
                throw new java.io.FileNotFoundException(err)
            }

            def fileName   = srcFile.name
            def dotIndex   = fileName.lastIndexOf('.')
            def baseStem   = dotIndex > 0 ? fileName.substring(0, dotIndex) : fileName
            def extension  = dotIndex > 0 ? fileName.substring(dotIndex + 1) : ''
            def outName    = extension ? "${baseStem}.${extension}" : baseStem

            def targetName = (srcFile.name == outName) ? "isolated_${outName}" : outName
            def localDest  = new java.io.File(task.workDir.toFile(), targetName)
            def outputDest = new java.io.File(targetBinDir, targetName)

            java.nio.file.Files.copy(srcFile.toPath(), localDest.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)
            java.nio.file.Files.copy(srcFile.toPath(), outputDest.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)

            BaseFileSystemUtils.appendLog(participantLog, "[L1] [INFO] [NATIVE_SPLIT] Successfully isolated file: ${targetName}")

        } catch (Throwable t) {
            def fatalErr = "[NATIVE_SPLIT] CRITICAL ERROR for participant '${currentId}': ${t.message}"
            if (participantLog != null && BaseFileSystemUtils != null) {
                BaseFileSystemUtils.appendLog(participantLog, "[L1] [ERROR] ${fatalErr}")
            }
            throw new RuntimeException(fatalErr, t)
        }
}