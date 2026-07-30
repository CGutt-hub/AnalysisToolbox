package lib

class ModuleUtils {
    /**
     * Standardized name resolution for NATIVE_MODULE execution steps.
     * Enforces appending rule: {id}_{declared}_{prefix1}_{prefix2}...
     */
    static String resolveName(Object id, Object inputParquetName, Object scriptPath) {
        def rawInput = inputParquetName ? new File(inputParquetName.toString()).name : ""
        if (!rawInput) return "metadata_matrix"

        // Step 1: Strip out all double or trailing .parquet extensions
        def base = rawInput.replaceAll(/(?i)(\.parquet)+$/, '')

        // Step 2: Extract script prefix (e.g., 'modules/analyzers/amplitude_analyzer.py' -> 'amplitude')
        def rawScriptName = scriptPath ? scriptPath.toString().tokenize('/').last().replace('.py', '') : ""
        def prefix = rawScriptName.replace('_analyzer', '').replace('_processor', '').replace('_consolidator', '')

        if (!prefix) {
            return base
        }

        // Step 3: Prevent duplicate appending if already present at the end of base
        if (base.endsWith("_${prefix}")) {
            return base
        }

        return "${base}_${prefix}"
    }
}