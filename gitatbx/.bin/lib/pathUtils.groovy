package lib

import lib.base.BaseIdentifierUtils

class PathUtils extends BaseIdentifierUtils {

    /**
     * Extracts, cleans, and sanitizes Parquet path strings from scalar or collection inputs.
     */
    static List<String> extractParquetPaths(Object incomingSignals) {
        if (!incomingSignals) return []

        def trackingQueue = []
        if (incomingSignals instanceof Collection) {
            trackingQueue.addAll(incomingSignals)
        } else if (incomingSignals.getClass().isArray()) {
            trackingQueue.addAll(incomingSignals as List)
        } else {
            trackingQueue.add(incomingSignals)
        }

        def cleanFiles = []
        int currentIndex = 0
        while (currentIndex < trackingQueue.size()) {
            def element = trackingQueue.get(currentIndex)
            if (element instanceof Collection) {
                trackingQueue.addAll(element)
            } else if (element != null && element.getClass().isArray()) {
                trackingQueue.addAll(element as List)
            } else if (element != null) {
                def plainPath = element.toString().replaceAll(/[\[\]\"\']/, "").trim()
                if (plainPath.endsWith('.parquet') && !plainPath.contains('null')) {
                    def sanitized = plainPath.replaceAll(/(?i)(\.parquet)+$/, '') + '.parquet'
                    cleanFiles.add(sanitized)
                }
            }
            currentIndex++
        }
        return cleanFiles.unique()
    }

    /**
     * Standardized step name resolution.
     * Enforces appending rule: {base}_{prefix} while preventing duplicate suffixes.
     */
    static String resolveStepName(Object _id, Object inputParquetName, Object scriptPath) {
        def rawInput = inputParquetName ? new File(inputParquetName.toString()).name : ""
        if (!rawInput) return "metadata_matrix"

        def base = rawInput.replaceAll(/(?i)(\.parquet)+$/, '')
        def rawScriptName = scriptPath ? scriptPath.toString().tokenize('/').last().replace('.py', '') : ""
        def prefix = rawScriptName.replace('_analyzer', '').replace('_processor', '').replace('_consolidator', '')

        if (!prefix || base.endsWith("_${prefix}")) {
            return base
        }

        return "${base}_${prefix}"
    }

    /**
     * Standardized output filename resolution contextually mapped to resolveStepName.
     */
    static String resolveName(Object id, Object inputParquetName, Object scriptPath) {
        return resolveStepName(id, inputParquetName, scriptPath)
    }
}