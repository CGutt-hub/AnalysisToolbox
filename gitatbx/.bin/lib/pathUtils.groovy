package lib

import lib.base.BaseIdentifierUtils
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.nio.file.NoSuchFileException

class PathUtils {

    static String globToRegex(String glob) {
        if (glob == null || glob.trim().isEmpty()) {
            return ".*"
        }
        
        String pattern = glob.trim()
        StringBuilder sb = new StringBuilder('^')
        
        for (int i = 0; i < pattern.length(); i++) {
            char c = pattern.charAt(i)
            switch (c) {
                case '*':
                    sb.append(".*")
                    break
                case '?':
                    sb.append(".")
                    break
                case '.':
                case '\\':
                case '[':
                case ']':
                case '{':
                case '}':
                case '(':
                case ')':
                case '+':
                case '^':
                case '$':
                case '|':
                    sb.append("\\").append(c)
                    break
                default:
                    sb.append(c)
            }
        }
        sb.append('$')
        return sb.toString()
    }

    static String cleanId(String rawName) {
        if (!rawName || rawName.trim().isEmpty()) {
            throw new IllegalArgumentException("[PathUtils] FATAL: Cannot clean null or empty identifier string.")
        }
        return BaseIdentifierUtils.cleanId(rawName)
    }

    static String extractParticipantId(Object item) {
        if (item == null) {
            throw new IllegalArgumentException("[PathUtils] FATAL: Null item provided for participant ID extraction.")
        }

        if (item instanceof Collection || item.getClass().isArray()) {
            List list = item instanceof Collection ? (List) item : (item as List)
            if (list.isEmpty()) {
                throw new IllegalArgumentException("[PathUtils] FATAL: Empty list provided for participant ID extraction.")
            }
            // If the first element is a metadata token rather than a file path, inspect it
            if (list.size() >= 2 && list[0] != null && !(list[0] instanceof Path) && !(list[0] instanceof File)) {
                String candidate = list[0].toString().trim()
                if (!candidate.isEmpty() && !candidate.toLowerCase().endsWith('.parquet')) {
                    return cleanId(candidate)
                }
            }
            return extractParticipantId(list[-1])
        }

        String pathStr  = item.toString().replaceAll('[\\[\\]"\\\']', '').trim()
        String fileName = Paths.get(pathStr).fileName?.toString() ?: pathStr

        // Agnostic extraction: Try splitting by standard delimiters or fallback to base name
        def matcher = (fileName =~ /^([^_]+(?:_[^_]+)?)/)
        if (matcher) {
            return cleanId(matcher[0][1])
        }

        String fallback = fileName.replaceAll(/\..*$/, '')
        if (fallback.isEmpty()) {
            throw new IllegalStateException("[PathUtils] FATAL: Failed to derive valid participant ID from input: ${item}")
        }
        return cleanId(fallback)
    }

    static Object extractFilePath(Object item) {
        if (item == null) {
            throw new IllegalArgumentException("[PathUtils] FATAL: Null item provided for file path extraction.")
        }
        if (item instanceof Collection || item.getClass().isArray()) {
            List list = item instanceof Collection ? (List) item : (item as List)
            if (list.isEmpty()) {
                throw new IllegalArgumentException("[PathUtils] FATAL: Empty list provided for file path extraction.")
            }
            return list[-1]
        }
        return item
    }

    static List<String> extractParquetPaths(Object incoming) {
        if (incoming == null) {
            throw new IllegalArgumentException("[PathUtils] FATAL: Incoming object for parquet extraction cannot be null.")
        }

        List<Object> trackingQueue = []
        if (incoming instanceof Collection) {
            trackingQueue.addAll(incoming as Collection)
        } else if (incoming.getClass().isArray()) {
            trackingQueue.addAll(incoming as List)
        } else {
            trackingQueue.add(incoming)
        }

        Set<String> results = [] as Set
        trackingQueue.each { item ->
            if (item != null) {
                String plainPath = item.toString().replaceAll('[\\[\\]"\\\']', '').trim()
                if (plainPath.toLowerCase().endsWith('.parquet')) {
                    Path p = Paths.get(plainPath).toAbsolutePath()
                    if (Files.exists(p) && Files.isRegularFile(p) && Files.size(p) > 0) {
                        results.add(p.toString())
                    }
                }
            }
        }

        if (results.isEmpty()) {
            throw new IllegalStateException("[PathUtils] FATAL: Zero valid Parquet file paths could be extracted from input collection.")
        }
        return results as List<String>
    }

    static void runSplit(Object moduleDir, Object task, Object params, Object contextPathObj, Object fullPattern) {
        assert params.output_dir   : "[NATIVE_SPLIT] FATAL: Missing mandatory parameter 'params.output_dir'"
        assert params.project_name : "[NATIVE_SPLIT] FATAL: Missing mandatory parameter 'params.project_name'"
        assert fullPattern         : "[NATIVE_SPLIT] FATAL: Mandatory parameter 'full_pattern' is null or empty"

        def patternStr = fullPattern.toString().trim()
        def filePrefix = extractParticipantId(contextPathObj) as String
        assert filePrefix && filePrefix != "unknown" : "[NATIVE_SPLIT] FATAL: Failed to derive participant ID from input context."

        def textLogPath = new File(task.workDir.toFile(), "${filePrefix}.log")

        def baseFsFile = [
            moduleDir.resolve('../lib/base/BaseFileSystemUtils.groovy').toFile(),
            moduleDir.resolve('../../lib/base/BaseFileSystemUtils.groovy').toFile()
        ].find { File f -> f.exists() }
        def BaseFileSystemUtils = baseFsFile ? new GroovyClassLoader(Thread.currentThread().contextClassLoader).parseClass(baseFsFile) : null

        try {
            if (BaseFileSystemUtils) {
                BaseFileSystemUtils.appendLog(textLogPath, "[L1] [INFO] [NATIVE_SPLIT] [${patternStr}] Isolating pattern '${patternStr}' for participant '${filePrefix}'.")
            }

            Path contextPath = null
            if (contextPathObj instanceof Path) {
                contextPath = (Path) contextPathObj
            } else if (contextPathObj != null) {
                contextPath = Paths.get(contextPathObj.toString().replaceAll('[\\[\\]"\\\']', '').trim())
            }

            if (contextPath == null || !Files.exists(contextPath)) {
                throw new NoSuchFileException("[NATIVE_SPLIT] FATAL: Context path does not exist: ${contextPathObj}")
            }

            File srcFile = null
            if (Files.isRegularFile(contextPath)) {
                srcFile = contextPath.toFile()
            } else if (Files.isDirectory(contextPath)) {
                String regexStr = globToRegex(patternStr)
                File[] matchedFiles = contextPath.toFile().listFiles { File fileObj ->
                    fileObj.isFile() && fileObj.name ==~ regexStr
                }
                if (matchedFiles != null && matchedFiles.length > 0) {
                    srcFile = matchedFiles[0]
                }
            }

            if (srcFile == null || !srcFile.exists()) {
                throw new FileNotFoundException("[NATIVE_SPLIT] FATAL: Missing input file matching pattern '${patternStr}' inside '${contextPath}'.")
            }

            String fileName   = srcFile.name
            int dotIndex      = fileName.lastIndexOf('.')
            String baseStem   = dotIndex > 0 ? fileName.substring(0, dotIndex) : fileName
            String extension  = dotIndex > 0 ? fileName.substring(dotIndex + 1) : ''
            String outName    = extension ? "${baseStem}.${extension}" : baseStem
            String targetName = (srcFile.name == outName) ? "isolated_${outName}" : outName

            File localDest = new File(task.workDir.toFile(), targetName)
            Files.copy(srcFile.toPath(), localDest.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)

            if (BaseFileSystemUtils) {
                BaseFileSystemUtils.appendLog(textLogPath, "[L1] [INFO] [NATIVE_SPLIT] [${patternStr}] Successfully isolated target file '${targetName}'.")
            }

        } catch (Throwable t) {
            if (BaseFileSystemUtils) {
                BaseFileSystemUtils.appendLog(textLogPath, "[L1] [ERROR] [NATIVE_SPLIT] [${patternStr}] Data execution failed: ${t.message}")
            }
            throw t
        }
    }
}