package lib

class CohortUtils {
    static List<String> extractPaths(Object incomingSignals) {
        if (!incomingSignals) return []
        return incomingSignals.toString()
            .replaceAll(/\[|\]/, '')
            .tokenize(',')
            .collect { it.trim().replaceAll(/(?i)(\.parquet)+$/, '') + '.parquet' }
            .findAll { it.endsWith('.parquet') && !it.contains('null') }
    }
}