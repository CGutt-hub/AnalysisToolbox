// utils/gitSync.groovy

import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.concurrent.locks.ReentrantLock

class gitSync {
    private static final ReentrantLock gitLock = new ReentrantLock()

    static void syncRepository(def workflow, def params, String logMessage) {
        def binDir = new File("${workflow.launchDir}/${params.output_dir}/.bin")
        if (!binDir.exists()) binDir.mkdirs()
        def pipelineLog = new File(binDir, "${params.project_name}.log")
        
        try {
            def ts = new SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
            try { pipelineLog.append("[${ts}] [GitSync] Starting: ${logMessage}\n") } catch (Exception ignored) {}

            gitLock.lock()
            try {
                def outputRoot = new File("${workflow.launchDir}/${params.output_dir}").getAbsoluteFile()
                def gitRoot = outputRoot
                while (gitRoot != null && !new File(gitRoot, ".git").exists()) {
                    gitRoot = gitRoot.getParentFile()
                }
                if (!gitRoot) return

                def inheritedEnv = System.getenv().collect { k, v -> "${k}=${v}" } + ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
                
                def runGit = { cmd, timeoutSec = 30 ->
                    try {
                        def proc = cmd.execute(inheritedEnv, gitRoot)
                        def out = new StringBuilder(); def err = new StringBuilder()
                        def reader = Thread.start { proc.waitForProcessOutput(out, err) }
                        reader.join(timeoutSec * 1000L)
                        if (reader.isAlive()) {
                            proc.destroyForcibly()
                            return [exit: -1, out: "timeout"]
                        }
                        return [exit: proc.exitValue(), out: "${out}${err}"]
                    } catch (Exception e) { return [exit: -1, out: e.message] }
                }

                new File(gitRoot, ".git/index.lock").with { f -> if (f.exists()) f.delete() }
                runGit(["git", "config", "--global", "safe.directory", "*"], 5)
                runGit(["git", "rebase", "--abort"], 5)

                def syncPaths = []
                ["${params.project_name}_l1", params.l2_folder ?: "${params.project_name}_l2", ".bin"].each { sub ->
                    def fullPath = new File(outputRoot, sub)
                    if (fullPath.exists()) {
                        syncPaths << gitRoot.toPath().relativize(fullPath.toPath()).toString().replace('\\', '/')
                    }
                }
                if (!syncPaths) return

                def addRes = runGit(["git", "add", "-A"] + syncPaths, 60)
                if (addRes.exit != 0) return

                def statusRes = runGit(["git", "status", "--porcelain", "--cached"], 15)
                if (!statusRes.out?.trim()) return

                runGit(["git", "commit", "-m", "autosync: ${logMessage}"], 30)
                
                def pushRes = runGit(["git", "push"], 120)
                if (pushRes.exit != 0) {
                    def pullRes = runGit(["git", "pull", "--rebase"], 60)
                    if (pullRes.exit != 0) runGit(["git", "rebase", "--abort"], 5)
                    runGit(["git", "push"], 120)
                }
                
                try { pipelineLog.append("[${new SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())}] [GitSync] Complete\n") } catch (Exception ignored) {}
            } finally {
                gitLock.unlock()
            }
        } catch (Exception e) {
            try { pipelineLog.append("[ERROR] GitSync failed: ${e.message}\n") } catch (Exception ignored) {}
        }
    }
}