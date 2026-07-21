// DiscoveryUtils.groovy (im selben Ordner wie discovery.nf belassen)

import java.io.File

class DiscoveryUtils {
    // BEHOBEN: Rekursive Suchfunktion, um params.project_name oder .git sicher aufzulösen
    static File findGitRoot(File currentDir) {
        if (currentDir == null || currentDir.getPath() == "/") return null
        if (new File(currentDir, ".git").exists()) return currentDir
        return findGitRoot(currentDir.getParentFile())
    }

    // BEHOBEN: Thread-sichere Bootstrap-Git-Ausführung innerhalb der JVM
    static Map executeBootstrapGit(List cmd, List inheritedEnv, File gitRoot) {
        try {
            def proc = cmd.execute(inheritedEnv, gitRoot)
            def out = new StringBuilder()
            def err = new StringBuilder()
            def reader = Thread.start { proc.waitForProcessOutput(out, err) }
            reader.join(10000L) // 10 Sekunden maximales Bootstrap-Timeout
            if (reader.isAlive()) { 
                proc.destroy()
                reader.join(2000L)
                return [exit: -1, out: "timeout"] 
            }
            out.append(err)
            return [exit: proc.exitValue(), out: out.toString()]
        } catch (Exception e) { 
            return [exit: -1, out: e.message] 
        }
    }

    // Universeller String-Cleaner für Probanden-IDs (Sichert Cross-OS Pfade)
    static String cleanParticipantId(String rawPid) {
        if (!rawPid) return "unknown"
        return rawPid.replaceAll('\r', '').trim().replaceAll(/[^A-Za-z0-9._-]/, '_')
    }
}