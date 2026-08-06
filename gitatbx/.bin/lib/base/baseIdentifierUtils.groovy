package lib.base

abstract class BaseIdentifierUtils {

    static String cleanId(String rawId, String fallback = "unknown") {
        if (!rawId) return fallback
        return rawId.replaceAll('\r', '').trim().replaceAll(/[^A-Za-z0-9._-]/, '_')
    }

    static String globToRegex(String globPattern) {
        if (!globPattern) return ".*"
        return globPattern.replaceAll(/\*/, '.*').replaceAll(/\?/, '.')
    }
}