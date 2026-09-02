package lib.base

import java.io.File
import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.channels.OverlappingFileLockException
import java.nio.file.StandardOpenOption
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

    /**
     * Normalizes relative execution paths (e.g. '../../EV_results') into canonical
     * repository-relative paths so Git commands do not escape the working tree.
     */
    private static String resolveRepoRelativePath(File gitRoot, String rawPathStr) {
        if (!rawPathStr || rawPathStr == "." || rawPathStr == "./") return "."
        try {
            File targetFile = new File(rawPathStr).getCanonicalFile()
            File canonicalRoot = gitRoot.getCanonicalFile()
            
            String rootPath = canonicalRoot.getAbsolutePath()
            String targetPath = targetFile.getAbsolutePath()

            if (targetPath.startsWith(rootPath)) {
                String rel = targetPath.substring(rootPath.length()).replace('\\', '/')
                if (rel.startsWith('/')) rel = rel.substring(1)
                return rel.isEmpty() ? "." : rel
            }
            return canonicalRoot.toPath().relativize(targetFile.toPath()).toString()
        } catch (Exception e) {
            return rawPathStr
        }
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

    /**
     * Cross-process lock wrapper ensuring both intra-JVM thread safety and inter-process OS locking
     * so concurrent participant finalizations wait for each other cleanly.
     */
    private static <T> T withCrossProcessGitLock(File gitRoot, Closure<T> action) {
        if (!gitRoot) throw new RuntimeException("CRITICAL: Git root is null.")

        GIT_LOCK.lock()
        File lockFile = new File(gitRoot, ".git/nextflow_git_sync.lock")
        FileChannel channel = null
        FileLock lock = null

        try {
            File parentDir = lockFile.parentFile
            if (parentDir != null && !parentDir.exists()) {
                parentDir.mkdirs()
            }

            channel = FileChannel.open(
                lockFile.toPath(),
                StandardOpenOption.CREATE,
                StandardOpenOption.WRITE
            )

            int retries = 0
            int maxRetries = 1200 // Wait up to 5 minutes for competing processes

            while (retries < maxRetries) {
                try {
                    lock = channel.tryLock()
                    if (lock != null) break
                } catch (OverlappingFileLockException e) {
                    // Lock held by another thread or JVM
                }
                retries++
                Thread.sleep(250)
            }

            if (lock == null) {
                throw new RuntimeException("CRITICAL: Timed out waiting for cross-process Git sync lock on ${lockFile.path}")
            }

            cleanupLocks(gitRoot)
            return action.call()
        } finally {
            if (lock != null && lock.isValid()) {
                try { lock.release() } catch (Throwable ignored) {}
            }
            if (channel != null) {
                try { channel.close() } catch (Throwable ignored) {}
            }
            GIT_LOCK.unlock()
        }
    }

    static void syncBootstrap(File gitRoot, String outputRel, String binRel, List<String> inheritedEnv) {
        if (!gitRoot) throw new RuntimeException("CRITICAL: Git root is null during bootstrap sync.")
        withCrossProcessGitLock(gitRoot) {
            String cleanBin = resolveRepoRelativePath(gitRoot, binRel)
            String cleanOutput = resolveRepoRelativePath(gitRoot, outputRel)

            executeGit(["git", "rm", "-r", "--cached", "--ignore-unmatch", "--", cleanBin], inheritedEnv, gitRoot)

            def addAll = executeGit(["git", "add", "-A", cleanOutput], inheritedEnv, gitRoot)
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
        withCrossProcessGitLock(gitRoot) {
            def inheritedEnv = System.getenv().collect { k, v -> "${k}=${v}" } + ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
            
            String cleanPath = resolveRepoRelativePath(gitRoot, relativePath)

            def addCmd = executeGit(["git", "add", "-A", cleanPath], inheritedEnv, gitRoot)
            if (addCmd.exit != 0) {
                throw new RuntimeException("Git add failed for path '${cleanPath}' (raw: '${relativePath}'): ${addCmd.out}")
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
        withCrossProcessGitLock(gitRoot) {
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