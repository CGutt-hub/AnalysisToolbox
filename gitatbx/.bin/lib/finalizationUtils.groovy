package lib

import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption

class FinalizationUtils {

    /**
     * Safely flattens and converts Nextflow AST objects (GString, Path, Dataflow, Lists)
     * into pure Java String path representations.
     */
    static List<String> sanitizePathList(Object input) {
        if (input == null) return []
        List<Object> rawList = []
        
        def flatten
        flatten = { obj ->
            if (obj == null) return
            if (obj instanceof Collection || obj.getClass().isArray()) {
                obj.each { flatten(it) }
            } else {
                rawList.add(obj)
            }
        }
        flatten(input)

        List<String> cleanPaths = []
        for (Object item : rawList) {
            if (item == null) continue
            String strPath = item.toString().trim()
            if (!strPath.isEmpty()) {
                cleanPaths.add(strPath)
            }
        }
        return cleanPaths
    }

    /**
     * Engine Probe 1: Validates signal files exist and exceed minimum byte size threshold.
     */
    static List<String> validateSignals(Object signals, long minSizeBytes = 12L) {
        List<String> paths = sanitizePathList(signals)
        if (paths.isEmpty()) return []

        List<String> validPaths = []
        for (String p : paths) {
            try {
                def f = new java.io.File(p)
                if (f.exists() && f.isFile() && f.length() >= minSizeBytes) {
                    validPaths.add(f.canonicalPath)
                }
            } catch (Throwable ignored) {
                // Ignore invalid file paths cleanly
            }
        }
        return validPaths
    }

    /**
     * Engine Probe 2: Promotes verified signal files into the target output root directory.
     * Returns structured execution map.
     */
    static Map<String, Object> promoteSignals(List<String> filePaths, java.io.File targetRootDir) {
        try {
            if (!targetRootDir.exists() && !targetRootDir.mkdirs()) {
                return [success: false, error: "Failed to create target directory: ${targetRootDir.absolutePath}"]
            }

            List<String> promotedNames = []
            for (String signal : filePaths) {
                def srcFile = new java.io.File(signal)
                def destFile = new java.io.File(targetRootDir, srcFile.name)
                Files.copy(srcFile.toPath(), destFile.toPath(), StandardCopyOption.REPLACE_EXISTING)
                promotedNames.add(srcFile.name)
            }

            return [success: true, promotedFiles: promotedNames]
        } catch (Throwable e) {
            return [
                success: false, 
                error: "Signal promotion error [${e.getClass().simpleName}]: ${e.message}"
            ]
        }
    }

    /**
     * Engine Probe 3: Gracefully cleans up transient log lock files.
     */
    static boolean removeLockFile(java.io.File binDir) {
        try {
            def logLock = new java.io.File(binDir, "log.lock")
            if (logLock.exists()) {
                return logLock.delete()
            }
            return true
        } catch (Throwable ignored) {
            return false
        }
    }

    /**
     * Engine Probe 4: Discovers Git repository root safely.
     */
    static java.io.File findGitRoot(Class baseGitUtilsClass, java.io.File launchDir) {
        try {
            return (java.io.File) baseGitUtilsClass.findGitRoot(launchDir)
        } catch (Throwable ignored) {
            return null
        }
    }

    /**
     * Engine Probe 5: Executes cross-process locked Git sync.
     * Returns structured execution map.
     */
    static Map<String, Object> executeGitSync(Class baseGitUtilsClass, java.io.File gitRoot, String targetDirPath, String commitMessage) {
        try {
            baseGitUtilsClass.syncPath(gitRoot, targetDirPath, commitMessage)
            return [success: true, error: null]
        } catch (Throwable e) {
            def causeMsg = e.cause ? " (Cause: ${e.cause.message})" : ""
            return [
                success: false, 
                error: "Git Sync Engine Error [${e.getClass().simpleName}]: ${e.message}${causeMsg}"
            ]
        }
    }
}