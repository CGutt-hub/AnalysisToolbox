// utils/gitSync.groovy

import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.concurrent.TimeUnit
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
                if (!gitRoot) {
                    try { pipelineLog.append("[${ts}] [GitSync] WARNING: No .git directory found walking up from ${outputRoot}\n") } catch (Exception ignored) {}
                    return
                }

                def runGit = { List<String> cmd, int timeoutSec = 30 ->
                    try {
                        def pb = new ProcessBuilder(cmd)
                        pb.directory(gitRoot)
                        
                        def env = pb.environment()
                        env.putAll(System.getenv())
                        env.put("GIT_TERMINAL_PROMPT", "0")
                        env.put("GIT_ASKPASS", "echo")
                        env.put("SSH_ASKPASS", "echo")
                        
                        pb.redirectErrorStream(true)
                        def proc = pb.start()
                        
                        def out = new StringBuilder()
                        def reader = Thread.start {
                            try {
                                proc.inputStream.eachLine { line -> out.append(line).append("\n") }
                            } catch (Exception ignored) {}
                        }
                        
                        boolean finished = proc.waitFor(timeoutSec, TimeUnit.SECONDS)
                        if (!finished) {
                            proc.destroyForcibly()
                            return [exit: -1, out: "timeout"]
                        }
                        
                        reader.join(1000L)
                        return [exit: proc.exitValue(), out: out.toString()]
                    } catch (Exception e) { 
                        return [exit: -1, out: e.message] 
                    }
                }

                def lockFile = new File(gitRoot, ".git/index.lock")
                if (lockFile.exists() && (System.currentTimeMillis() - lockFile.lastModified() > 10000)) {
                    lockFile.delete()
                }

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
                if (addRes.exit != 0) {
                    try { pipelineLog.append("[ERROR] GitSync git add failed: ${addRes.out}\n") } catch (Exception ignored) {}
                    return
                }

                def statusRes = runGit(["git", "status", "--porcelain", "--cached"], 15)
                if (!statusRes.out?.trim()) return

                def commitRes = runGit(["git", "commit", "-m", "autosync: ${logMessage}"], 30)
                if (commitRes.exit != 0) {
                    try { pipelineLog.append("[ERROR] GitSync commit failed: ${commitRes.out}\n") } catch (Exception ignored) {}
                    return
                }
                
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