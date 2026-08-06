package lib.base

import java.io.File
import java.util.concurrent.locks.ReentrantLock

abstract class BaseGitUtils {

    private static final ReentrantLock GIT_LOCK = new ReentrantLock()

    static File findGitRoot(File currentDir) {
        if (currentDir == null || currentDir.getPath() == "/") {
            throw new RuntimeException("CRITICAL: Git repository root not found starting from path: ${currentDir}")
        }
        if (new File(currentDir, ".git").exists()) return currentDir
        return findGitRoot(currentDir.getParentFile())
    }

    static Map executeGit(List<String> cmd, List<String> inheritedEnv, File gitRoot, long timeoutMs = 10000L) {
        try {
            def proc = cmd.execute(inheritedEnv, gitRoot)
            def out = new StringBuilder()
            def err = new StringBuilder()
            def reader = Thread.start { proc.waitForProcessOutput(out, err) }
            reader.join(timeoutMs)
            if (reader.isAlive()) { 
                proc.destroy()
                reader.join(2000L)
                throw new RuntimeException("Git command timed out: ${cmd.join(' ')}")
            }
            out.append(err)
            return [exit: proc.exitValue(), out: out.toString()]
        } catch (Exception e) { 
            throw new RuntimeException("Git execution failed for command '${cmd.join(' ')}': ${e.message}", e)
        }
    }

    static void cleanupLocks(File gitRoot) {
        if (!gitRoot) return
        new File(gitRoot, ".git/index.lock").delete()
        new File(gitRoot, ".git/rebase-merge").deleteDir()
        new File(gitRoot, ".git/rebase-apply").deleteDir()
    }

    static void syncBootstrap(File gitRoot, String outputRel, String binRel, List<String> inheritedEnv) {
        if (!gitRoot) throw new RuntimeException("CRITICAL: Git root is null during bootstrap sync.")
        GIT_LOCK.withLock {
            cleanupLocks(gitRoot)

            executeGit(["git", "rm", "-r", "--cached", "--ignore-unmatch", "--", binRel], inheritedEnv, gitRoot)

            def addAll = executeGit(["git", "add", "-A", outputRel], inheritedEnv, gitRoot)
            if (addAll.exit != 0) {
                throw new RuntimeException("Git bootstrap add failed: ${addAll.out}")
            }

            def st = executeGit(["git", "status", "--porcelain", "--cached"], inheritedEnv, gitRoot)
            if (st.out?.trim()) {
                def ts = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                def commit = executeGit(["git", "commit", "-m", "autosync: bootstrap push before analysis (${ts})"], inheritedEnv, gitRoot)
                if (commit.exit != 0) {
                    throw new RuntimeException("Git bootstrap commit failed: ${commit.out}")
                }
                
                def pull = executeGit(["git", "pull", "--rebase"], inheritedEnv, gitRoot)
                if (pull.exit != 0) {
                    executeGit(["git", "rebase", "--abort"], inheritedEnv, gitRoot)
                    cleanupLocks(gitRoot)
                    throw new RuntimeException("Git bootstrap pull/rebase failed: ${pull.out}")
                }

                def push = executeGit(["git", "push"], inheritedEnv, gitRoot)
                if (push.exit != 0) {
                    throw new RuntimeException("Git bootstrap push failed: ${push.out}")
                }
            }
        }
    }

    static void syncPath(File gitRoot, String relativePath, String commitMessage) {
        if (!gitRoot) throw new RuntimeException("CRITICAL: Git root is null during targeted sync for path: ${relativePath}")
        GIT_LOCK.withLock {
            cleanupLocks(gitRoot)
            def inheritedEnv = System.getenv().collect { k, v -> "${k}=${v}" } + ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
            
            def addCmd = executeGit(["git", "add", "-A", relativePath], inheritedEnv, gitRoot)
            if (addCmd.exit != 0) {
                throw new RuntimeException("Git add failed for path '${relativePath}': ${addCmd.out}")
            }

            def st = executeGit(["git", "status", "--porcelain", "--cached"], inheritedEnv, gitRoot)
            if (st.out?.trim()) {
                def commitCmd = executeGit(["git", "commit", "-m", commitMessage], inheritedEnv, gitRoot)
                if (commitCmd.exit != 0) {
                    throw new RuntimeException("Git commit failed: ${commitCmd.out}")
                }

                def pullCmd = executeGit(["git", "pull", "--rebase"], inheritedEnv, gitRoot)
                if (pullCmd.exit != 0) {
                    executeGit(["git", "rebase", "--abort"], inheritedEnv, gitRoot)
                    cleanupLocks(gitRoot)
                    throw new RuntimeException("Git pull --rebase failed: ${pullCmd.out}")
                }

                def pushCmd = executeGit(["git", "push"], inheritedEnv, gitRoot)
                if (pushCmd.exit != 0) {
                    throw new RuntimeException("Git push failed: ${pushCmd.out}")
                }
            }
        }
    }

    static void syncRepository(File gitRoot, String commitMessage) {
        if (!gitRoot) throw new RuntimeException("CRITICAL: Git root is null during repository-wide sync.")
        GIT_LOCK.withLock {
            cleanupLocks(gitRoot)
            def inheritedEnv = System.getenv().collect { k, v -> "${k}=${v}" } + ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
            
            def addAll = executeGit(["git", "add", "-A"], inheritedEnv, gitRoot)
            if (addAll.exit != 0) {
                throw new RuntimeException("Git repository-wide add failed: ${addAll.out}")
            }

            def st = executeGit(["git", "status", "--porcelain", "--cached"], inheritedEnv, gitRoot)
            if (st.out?.trim()) {
                def commitCmd = executeGit(["git", "commit", "-m", commitMessage], inheritedEnv, gitRoot)
                if (commitCmd.exit != 0) {
                    throw new RuntimeException("Git commit failed: ${commitCmd.out}")
                }

                def pullCmd = executeGit(["git", "pull", "--rebase"], inheritedEnv, gitRoot)
                if (pullCmd.exit != 0) {
                    executeGit(["git", "rebase", "--abort"], inheritedEnv, gitRoot)
                    cleanupLocks(gitRoot)
                    throw new RuntimeException("Git pull --rebase failed: ${pullCmd.out}")
                }

                def pushCmd = executeGit(["git", "push"], inheritedEnv, gitRoot)
                if (pushCmd.exit != 0) {
                    throw new RuntimeException("Git push failed: ${pushCmd.out}")
                }
            }
        }
    }
}