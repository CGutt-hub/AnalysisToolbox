import java.util.concurrent.locks.ReentrantLock

// Default for optional params (avoids "Access to undefined parameter" warnings)
if (!params.containsKey('l2_folder')) params.l2_folder = null

// Shared git lock for sequential git operations across participants
@groovy.transform.Field
static java.util.concurrent.locks.ReentrantLock git_lock = new java.util.concurrent.locks.ReentrantLock()

// Auto-register final sync on pipeline shutdown (fires on Ctrl+C / SIGINT).
// In a watchPath-based pipeline this is the only way the workflow ends,
// so this effectively runs "on keyboard interrupt".
workflow.onComplete { finalSync() }

// Configure git user identity once at startup
@groovy.transform.Field
static boolean git_configured = false

def configureGitUser() {
    if (!git_configured) {
        try {
            def git_root = new File(workflow.launchDir.toString())
            while (git_root != null && !new File(git_root, ".git").exists()) {
                git_root = git_root.getParentFile()
            }
            if (git_root) {
                // Mark all repos as safe (cross-OS ownership mismatch: Windows + Linux)
                ["git", "config", "--global", "safe.directory", "*"].execute().waitFor()
                // Set git user config in repository (not global to avoid permission issues)
                ["git", "config", "user.email", params.git_user_email].execute(null, git_root).waitFor()
                ["git", "config", "user.name",  params.git_user_name ].execute(null, git_root).waitFor()
                git_configured = true
                
                // Log to ${params.project_name}.log instead of terminal
                def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}.log")
                pipeline_log.parentFile.mkdirs()
                def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                pipeline_log.append("[${timestamp}] [workflow] Git user configured: ${params.git_user_name} <${params.git_user_email}>\n")
            }
        } catch (Exception e) {
            def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}.log")
            pipeline_log.parentFile.mkdirs()
            def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
            pipeline_log.append("[${timestamp}] [workflow] Warning: Could not configure git user - ${e.message}\n")
        }
    }
}

// Enhanced participant_discovery: discovers participants, creates output folders
workflow participant_discovery {
    take:
        input_dir
        output_dir
        participant_pattern

    main:
        // Configure git user identity at workflow start
        configureGitUser()
        
        // Convert glob pattern to regex
        def regex_pattern = participant_pattern.replaceAll(/\*/, '.*').replaceAll(/\?/, '.')
        def input_path = new File("${workflow.launchDir}/${input_dir}")
        def output_path = new File("${workflow.launchDir}/${output_dir}")
        // Discover already-processed participants by checking per-participant output subfolders inside l1/
        // A .reinject marker overrides the check — the participant is re-included for correction replay.
        def l1_path    = new File(output_path, "${params.project_name}_l1")
        def output_dirs = l1_path.exists() ? l1_path.list() as Set : [] as Set
        def reinject_pids = l1_path.exists()
            ? l1_path.listFiles().findAll { it.isDirectory() && new File(it, ".reinject").exists() }.collect { it.name } as Set
            : [] as Set
        def new_participants = input_path.list().findAll { it.matches(regex_pattern) }.findAll { !(it in output_dirs) || it in reinject_pids }
        
        // Create .bin/ infrastructure directory and global log inside it
        def bin_dir_infra = new File("${workflow.launchDir}/${params.output_dir}", ".bin")
        bin_dir_infra.mkdirs()
        def pipeline_log = new File(bin_dir_infra, "${params.project_name}.log")
        if (!pipeline_log.exists()) {
            pipeline_log.text = ""
        }

        // Create l2 folder + log at startup if this workflow contains second-level analyses
        if (params.l2_analyses) {
            def l2_dir  = new File("${workflow.launchDir}/${params.output_dir}", "${params.project_name}_l2")
            def l2_plots_dir = new File(l2_dir, "plots")
            def l2_tables_dir = new File(l2_dir, "tables")
            def l2_results_dir = new File(l2_dir, "results")
            l2_dir.mkdirs()
            l2_plots_dir.mkdirs()
            l2_tables_dir.mkdirs()
            l2_results_dir.mkdirs()
            // l2 log is a .log.parquet written live by IOInterface bash block — no text file needed here
        }

        // Create l1 parent folder
        def l1_dir_scaffold = new File("${workflow.launchDir}/${params.output_dir}", "${params.project_name}_l1")
        l1_dir_scaffold.mkdirs()

        // Initialize HTML archive (shared across participants)
        // To view results: atbx serve --dir <results_dir>
        def html_file_scaffold = new File(bin_dir_infra, "${params.project_name}_results.html")
        if (!html_file_scaffold.exists()) {
            try {
                def init_cmd = [params.python_exe, '-u',
                    "${workflow.launchDir}/${params.toolbox_dir}/bin/interactive_plotter.py",
                    'init', html_file_scaffold.absolutePath]
                def proc = init_cmd.execute()
                proc.waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
                if (proc.exitValue() != 0) {
                    pipeline_log.append(
                        "[${new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())}] [workflow] Warning: Could not initialize HTML archive: ${proc.text}\n"
                    )
                }
            } catch (Exception e) {
                pipeline_log.append("[${new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())}] [workflow] HTML init error: ${e.message}\n")
            }
        }

        // Clean up any leftover .finalized marker files from old pipeline runs
        output_path.eachDirRecurse { dir ->
            def marker = new File(dir, ".finalized")
            if (marker.exists()) marker.delete()
        }

        // Bootstrap push: commit & push ALL current changes after scaffold creation
        // so the remote is fully up-to-date before analyses start.
        try {
            def git_root = new File(workflow.launchDir.toString())
            while (git_root != null && !new File(git_root, ".git").exists()) {
                git_root = git_root.getParentFile()
            }
            if (git_root) {
                // Cache inherited env once — avoid re-collecting on every git call over SMB
                def inheritedEnv = System.getenv().collect { k, v -> "${k}=${v}" } + ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
                def runBootGit = { cmd, timeout = 10 ->
                    try {
                        def proc = cmd.execute(inheritedEnv, git_root)
                        def out = new StringBuilder(); def err = new StringBuilder()
                        def reader = Thread.start { proc.waitForProcessOutput(out, err) }
                        reader.join(timeout * 1000L)
                        if (reader.isAlive()) { proc.destroy(); reader.join(2000L); return [exit: -1, out: "timeout"] }
                        out.append(err)
                        return [exit: proc.exitValue(), out: out.toString()]
                    } catch (Exception e) { return [exit: -1, out: e.message] }
                }
                // Clean up stale lock / rebase state
                new File(git_root, ".git/index.lock").with { if (exists()) delete() }
                new File(git_root, ".git/rebase-merge").with { if (exists()) deleteDir() }
                new File(git_root, ".git/rebase-apply").with { if (exists()) deleteDir() }

                // Ensure Nextflow trace file and local runtime files are never tracked —
                // trace is held open the entire run and causes rebase failures;
                // the plain-text log and HTML viewer are local-only artifacts.
                def binIgnore = new File(bin_dir_infra, ".gitignore")
                def ignorePatterns = ["pipeline_trace.txt", "*.log", "*_results.html"]
                ignorePatterns.each { pattern ->
                    if (!binIgnore.exists() || !binIgnore.text.contains(pattern)) {
                        binIgnore.append("${pattern}\n")
                    }
                }
                // Single call to untrack all ignored files still in the index
                def binRel = git_root.toPath().relativize(bin_dir_infra.toPath().toAbsolutePath()).toString().replace('\\', '/')
                runBootGit(["git", "rm", "-r", "--cached", "--ignore-unmatch", "--", binRel], 15)
                // Untrack Nextflow runtime logs — held open during the run, breaks rebase
                def nfLog = new File(workflow.launchDir.toString(), ".nextflow.log")
                if (nfLog.exists()) {
                    def nfLogRel = git_root.toPath().relativize(nfLog.toPath().toAbsolutePath()).toString().replace('\\', '/')
                    runBootGit(["git", "rm", "--cached", "--ignore-unmatch", "--", nfLogRel], 5)
                }

                def outputRel = git_root.toPath().relativize(
                    new File("${workflow.launchDir}/${output_dir}").getAbsoluteFile().toPath()
                ).toString().replace('\\', '/')
                def addAll = runBootGit(["git", "add", "-A", outputRel], 30)
                if (addAll.exit == 0) {
                    def st = runBootGit(["git", "status", "--porcelain", "--cached"], 15)
                    if (st.out?.trim()) {
                        def ts = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        runBootGit(["git", "commit", "-m", "autosync: bootstrap push before analysis (${ts})"], 30)
                        // Pull with rebase but WITHOUT --autostash: we must not stash/restore
                        // uncommitted working-tree files (e.g. .nf/.config edited externally)
                        // because the stash-pop can silently fail on SMB/NFS and leave those
                        // files reverted to the remote HEAD version.
                        def pull = runBootGit(["git", "pull", "--rebase"], 60)
                        if (pull.exit != 0) {
                            runBootGit(["git", "rebase", "--abort"], 5)
                            new File(git_root, ".git/index.lock").with { if (exists()) delete() }
                        }
                        runBootGit(["git", "push"], 120)
                    }
                }
                pipeline_log.append("[${new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())}] [workflow] Bootstrap push completed\n")
            }
        } catch (Exception e) {
            pipeline_log.append("[${new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())}] [workflow] Bootstrap push failed: ${e.message}\n")
        }

        def all_participants
        if (params.watch) {
            def watched_participants = Channel
                .watchPath("${workflow.launchDir}/${input_dir}/*", 'create,modify')
                .map { path -> path.getName() }
                .filter { it.matches(regex_pattern) }
                .unique()
            all_participants = Channel.fromList(new_participants).concat(watched_participants)
                .filter { pid ->
                    def safe_id = pid.replaceAll('\r', '').trim().replaceAll('[^A-Za-z0-9._-]', '_')
                    def pid_dir = new File("${workflow.launchDir}/${output_dir}/${params.project_name}_l1/${safe_id}")
                    !pid_dir.exists() || new File(pid_dir, ".reinject").exists()
                }
        } else {
            all_participants = Channel.fromList(new_participants)
        }

        participant_context = all_participants.map { pid ->
            def safe_id = pid.replaceAll('\r', '').trim().replaceAll('[^A-Za-z0-9._-]', '_')
            // Per-participant subfolder: {output_dir}/{project_name}_l1/{safe_id}/
            def participant_dir = new File(l1_dir_scaffold, safe_id)
            participant_dir.mkdirs()
            
            // Log init happens in the IOInterface bash block on first task run (writes .log.parquet live).
            // Just append to the global pipeline log here.
            def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
            def global_pipeline_log = new File(new File("${workflow.launchDir}/${params.output_dir}", ".bin"), "${params.project_name}.log")
            global_pipeline_log.append("=== ${safe_id} initialized: ${timestamp} ===\nOutput: ${participant_dir}\n\n")
            
            def folder = "${output_dir}/${params.project_name}_l1/${safe_id}"
            [pid, folder]
        }

    emit:
        participant_context
}

// Extracted from .subscribe to avoid Groovy's maximum callback size limit.
// Handles per-participant logging, HTML archive update, and git sync.
def finalizeParticipant(pid, files, folder, finalizedPids) {
    if (!finalizedPids.add(pid)) return

    // Remove .reinject marker so this participant is not re-included on next run
    new File("${workflow.launchDir}/${folder}/.reinject").with { if (exists()) delete() }

    def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}.log")
    try {

    Thread.sleep(2000)

    def timestamp  = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
    def log_file   = new File("${workflow.launchDir}/${folder}/${pid}.log.parquet")
    def start_time = log_file.exists() ? new Date(log_file.lastModified()) : new Date()
    def duration   = (new Date().time - start_time.time) / 1000

    // Append finalization summary to the live .log.parquet
    def finalization_text = """
=== Analysis completed for ${pid}: ${timestamp} ===
Modules completed: ${files.size()}
Session: ${workflow.sessionId}
Duration: ${duration}s
=== ${pid} finalized: ${timestamp} ===

"""
    if (log_file.parentFile?.exists() || log_file.parentFile?.mkdirs()) {
        try {
            def append_cmd = [params.python_exe, '-u',
                "${workflow.launchDir}/${params.toolbox_dir}/bin/log_to_parquet.py",
                log_file.absolutePath, '--text', finalization_text]
            append_cmd.execute().waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
        } catch (Exception e) { /* non-critical */ }
    }

    // Register the live .log.parquet in the interactive HTML archive (meta.json)
    // add_log_to_archive detects .log.parquet and uses the file at its existing location.
    def procedure_html = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}_results.html")
    if (procedure_html.exists() && log_file.exists()) {
        try {
            def add_log_cmd = [params.python_exe, '-u',
                "${workflow.launchDir}/${params.toolbox_dir}/bin/interactive_plotter.py",
                'add-log', procedure_html.absolutePath, pid, log_file.absolutePath, 'Pipeline Log']
            def proc = add_log_cmd.execute()
            proc.waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
            if (proc.exitValue() != 0) {
                pipeline_log.append("Warning: Failed to register ${pid} log.parquet in HTML archive\n")
            }
        } catch (Exception e) {
            pipeline_log.append("Warning: Failed to register ${pid} log.parquet in HTML archive: ${e.message}\n")
        }
    }
    // .log.parquet is kept — it is the live-accessible record and is committed to git

    try {
        pipeline_log.append("\n=== Analysis completed for ${pid}: ${timestamp} ===\n")
        pipeline_log.append("Modules completed: ${files.size()}\n")
        pipeline_log.append("Session: ${workflow.sessionId}\n")
        pipeline_log.append("Duration: ${duration}s\n")
        pipeline_log.append("\n=== ${pid} finalized: ${timestamp} ===\n\n")
    } catch (Exception e) { /* .bin/ may not exist yet on NAS — non-critical */ }

    // Git sync
    git_lock.lock()
    try {
        def results_full_path = new File("${workflow.launchDir}/${folder}").getAbsoluteFile()
        def git_root = results_full_path
        while (git_root != null && !new File(git_root, ".git").exists()) {
            git_root = git_root.getParentFile()
        }
        if (!git_root) return

        def relative_path     = git_root.toPath().relativize(results_full_path.toPath()).toString().replace('\\', '/')
        def pipeline_log_path = git_root.toPath().relativize(pipeline_log.toPath()).toString().replace('\\', '/')
        def output_root_full  = new File("${workflow.launchDir}/${params.output_dir}").getAbsoluteFile()
        def bin_full          = new File(output_root_full, ".bin")
        def html_full         = new File(bin_full, "${params.project_name}_results.html")
        def meta_full         = new File(bin_full, "${params.project_name}_meta.json")
        def html_path         = html_full.exists() ? git_root.toPath().relativize(html_full.toPath()).toString().replace('\\', '/') : null
        def meta_path         = meta_full.exists() ? git_root.toPath().relativize(meta_full.toPath()).toString().replace('\\', '/') : null

        // Cache inherited env once — avoid re-collecting on every git call over SMB
        def inheritedEnv = System.getenv().collect { k, v -> "${k}=${v}" } + ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
        def runGit = { cmd, timeout = 10 ->
            try {
                def proc = cmd.execute(inheritedEnv, git_root)
                def out = new StringBuilder()
                def err = new StringBuilder()
                def reader = Thread.start { proc.waitForProcessOutput(out, err) }
                reader.join(timeout * 1000L)
                if (reader.isAlive()) {
                    // Kill the process tree — proc.destroy() only signals the parent;
                    // child processes (ssh, git-remote-https, git-lfs) survive on NFS.
                    try {
                        def pid_val = proc.pid()           // Java 9+
                        ["pkill", "-9", "-P", "${pid_val}"].execute().waitFor(3, java.util.concurrent.TimeUnit.SECONDS)
                    } catch (Exception ignored) {}
                    try { proc.destroyForcibly() } catch (Exception ignored) { proc.destroy() }
                    reader.join(3000L)
                    out.append(err)
                    return [exit: -1, out: "timeout: ${out}".toString()]
                }
                out.append(err)
                return [exit: proc.exitValue(), out: out.toString()]
            } catch (Exception e) {
                return [exit: -1, out: e.message]
            }
        }

        // Aggressively remove index.lock — on NFS a simple delete() can fail
        // when an orphaned git child still holds the file descriptor open.
        def nukeIndexLock = {
            def lockFile = new File(git_root, ".git/index.lock")
            if (!lockFile.exists()) return
            def maxTries = 8
            def delays = [0, 500, 2000, 5000, 10000, 15000, 20000, 30000] // ms
            def attempt = 0
            while (lockFile.exists() && attempt < maxTries) {
                def msg = "[autosync] index.lock present (attempt ${attempt + 1}/${maxTries})"
                pipeline_log.append("${msg}\n")
                // Try plain delete
                lockFile.delete()
                if (!lockFile.exists()) {
                    pipeline_log.append("[autosync] index.lock removed by delete()\n")
                    return
                }
                // Try fuser kill (if available)
                try {
                    ["fuser", "-k", lockFile.absolutePath].execute().waitFor(5, java.util.concurrent.TimeUnit.SECONDS)
                    Thread.sleep(500)
                } catch (Exception ignored) {}
                lockFile.delete()
                if (!lockFile.exists()) {
                    pipeline_log.append("[autosync] index.lock removed after fuser\n")
                    return
                }
                // Try killing git processes (if on Linux/Mac)
                try {
                    ["pkill", "-9", "git"].execute().waitFor(3, java.util.concurrent.TimeUnit.SECONDS)
                } catch (Exception ignored) {}
                lockFile.delete()
                if (!lockFile.exists()) {
                    pipeline_log.append("[autosync] index.lock removed after pkill git\n")
                    return
                }
                // Wait longer for NFS to release
                def waitMs = delays[Math.min(attempt, delays.size() - 1)]
                if (waitMs > 0) {
                    pipeline_log.append("[autosync] Waiting ${waitMs} ms for NFS lock release\n")
                    Thread.sleep(waitMs)
                }
                lockFile.delete()
                attempt++
            }
            if (lockFile.exists()) {
                pipeline_log.append("[autosync] ERROR: index.lock could not be removed after ${maxTries} attempts. Autosync aborted.\n")
                throw new RuntimeException("index.lock could not be removed after ${maxTries} attempts")
            }
            pipeline_log.append("[autosync] index.lock removed after ${attempt} attempts\n")
        }

        def cleanupRebase = {
            runGit(["git", "rebase", "--abort"], 2)
            new File(git_root, ".git/rebase-merge").with { if (exists()) deleteDir() }
            new File(git_root, ".git/rebase-apply").with { if (exists()) deleteDir() }
            nukeIndexLock()
        }

        def logSync = { status, details = "" ->
            try {
                def ts = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                pipeline_log.append("\n=== Git sync for ${pid}: ${ts} ===\n")
                pipeline_log.append("Path: ${relative_path}\n")
                pipeline_log.append("Status: ${status}\n")
                if (details) pipeline_log.append("${details}\n")
                pipeline_log.append("=== ${pid} sync complete ===\n\n")
            } catch (Exception e) { /* non-critical */ }
        }

        cleanupRebase()

        // Trust all repos regardless of filesystem ownership (cross-OS)
        runGit(["git", "config", "--global", "safe.directory", "*"], 5)

        // Helper: commit a list of paths and push.
        def commitAndPush = { List paths, String msg ->
            if (!paths) return true
            // Retry git-add up to 5 times — an external process (e.g. VS Code Git
            // extension) may race us in the window between nukeIndexLock() and the
            // actual git-add call, creating a fresh index.lock we didn't see.
            def addResult = null
            def addAttempts = 5
            for (def i = 0; i < addAttempts; i++) {
                nukeIndexLock()
                addResult = runGit(["git", "add", "-A"] + paths, 120)
                if (addResult.exit == 0) break
                if (!addResult.out?.contains('index.lock')) break  // unrelated error, don't retry
                pipeline_log.append("[autosync] git add retry ${i + 1}/${addAttempts}: index.lock race\n")
                Thread.sleep(1000L * (i + 1))  // 1s, 2s, 3s, 4s back-off
            }
            if (addResult.exit != 0) {
                logSync("Git add failed (${msg})", addResult.out)
                runGit(["git", "reset", "HEAD"], 10)
                return false
            }
            def st = runGit(["git", "status", "--porcelain", "--cached"], 15)
            if (!st.out?.trim()) return true  // nothing to commit
            def commitResult = runGit(["git", "commit", "-m", msg], 30)
            if (commitResult.exit != 0) {
                logSync("Commit failed (${msg})", commitResult.out)
                return false
            }
            def push = runGit(["git", "push"], 600)
            if (push.exit == 0) return true
            // Push rejected → pull --rebase then retry once (no --autostash: avoids clobbering
            // uncommitted working-tree edits on SMB/NFS where stash-pop can silently fail)
            def pull = runGit(["git", "pull", "--rebase"], 120)
            if (pull.exit != 0) {
                cleanupRebase()
                logSync("Pull --rebase failed (${msg}) — committed locally", pull.out)
                return true
            }
            push = runGit(["git", "push"], 600)
            if (push.exit != 0) {
                logSync("Push failed (${msg}) — committed locally", push.out)
            }
            return true
        }

        // Scoped sync: only participant folder, .bin/, and l2 (if present).
        // All output parquets are small (visualisation-only, <100 KB) so no
        // LFS or large-file splitting is needed.
        def binRel = git_root.toPath().relativize(bin_full.toPath()).toString().replace('\\', '/')
        def syncPaths = [relative_path, binRel]

        def l2_folder_name = params.l2_folder ?: "${params.project_name}_l2"
        def l2_full_dir = new File(output_root_full, l2_folder_name)
        if (l2_full_dir.exists() && l2_full_dir.isDirectory()) {
            syncPaths << git_root.toPath().relativize(l2_full_dir.toPath()).toString().replace('\\', '/')
        }

        // Convert pipeline log → parquet and register in HTML archive before commit
        def pipeline_log_parquet      = new File(pipeline_log.parentFile, "${params.project_name}.log.parquet")
        def pipeline_log_parquet_path = git_root.toPath().relativize(pipeline_log_parquet.toPath()).toString().replace('\\', '/')
        if (procedure_html.exists() && pipeline_log.exists()) {
            try {
                def add_global_cmd = [params.python_exe, '-u',
                    "${workflow.launchDir}/${params.toolbox_dir}/bin/interactive_plotter.py",
                    'add-log', procedure_html.absolutePath, 'global', pipeline_log.absolutePath, "${params.project_name}.log"]
                def proc2 = add_global_cmd.execute()
                proc2.waitFor(60, java.util.concurrent.TimeUnit.SECONDS)
            } catch (Exception e) { /* non-critical */ }
        }
        if (pipeline_log_parquet.exists()) {
            syncPaths << pipeline_log_parquet_path
        }

        // Single scoped commit + push
        commitAndPush(syncPaths, "autosync: ${pid} complete")
    } finally {
        git_lock.unlock()
    }

    } catch (Exception e) {
        def ts = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
        try { pipeline_log.append("[${ts}] [ERROR] finalizeParticipant(${pid}) failed: ${e.message}\n") } catch (Exception ignored) {}
    }
}

// Channel-based finalization: triggers per participant when all terminal result
// channels have emitted.  groupTuple(size: N) fires the moment N items arrive
// for a PID — works with infinite (watchPath) channels, no timers needed.
// Participants with upstream failures (< N results) are caught by finalSync()
// on Ctrl+C shutdown.
workflow finalize_participant {
    take:
        result_outputs       // Pre-mixed channel of all L1 terminal result files
        result_count         // Number of expected L1 terminal results per participant
        participant_context  // Channel of [pid, folder] tuples

    main:
        def finalizedPids = Collections.synchronizedSet(new HashSet<String>())
        def contextMap = new java.util.concurrent.ConcurrentHashMap<String, String>()

        participant_context.subscribe { pid, folder -> contextMap[pid] = folder }

        result_outputs
            .map { file -> [file.baseName.toString().split('_')[0..1].join('_'), file] }
            .groupTuple(size: result_count)
            .subscribe { pid, files ->
                def folder = contextMap[pid]
                if (folder) finalizeParticipant(pid, files, folder, finalizedPids)
            }
}

// L2 finalization: subscribes to L2 result channels.  Each emission triggers
// a dedicated L2 log entry.  Git sync of the L2 folder already happens inside
// every L1 finalizeParticipant (syncPaths includes l2_dir), so no separate
// commit/push is needed here — just the log bookkeeping.
workflow finalize_l2 {
    take:
        l2_outputs

    main:
        l2_outputs.subscribe { file ->
            def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}.log")
            def ts = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
            try { pipeline_log.append("[${ts}] L2 result: ${file.name}\n") } catch (Exception e) {}
        }
}

// Final sync: commit and push all scoped output paths once at pipeline close.
// Call this from workflow.onComplete in your pipeline:
//     workflow.onComplete { finalSync() }
def finalSync() {
    def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}.log")
    try {
        def ts = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
        try { pipeline_log.append("[${ts}] [workflow] Final sync starting\n") } catch (Exception ignored) {}

        git_lock.lock()
        try {
            def output_root_full = new File("${workflow.launchDir}/${params.output_dir}").getAbsoluteFile()
            def git_root = output_root_full
            while (git_root != null && !new File(git_root, ".git").exists()) {
                git_root = git_root.getParentFile()
            }
            if (!git_root) return

            // Cache inherited env once — avoid re-collecting on every git call over SMB
            def inheritedEnv = System.getenv().collect { k, v -> "${k}=${v}" } + ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
            def runGit = { cmd, timeout = 10 ->
                try {
                    def proc = cmd.execute(inheritedEnv, git_root)
                    def out = new StringBuilder(); def err = new StringBuilder()
                    def reader = Thread.start { proc.waitForProcessOutput(out, err) }
                    reader.join(timeout * 1000L)
                    if (reader.isAlive()) {
                        try { proc.destroyForcibly() } catch (Exception ignored) { proc.destroy() }
                        reader.join(3000L)
                        out.append(err)
                        return [exit: -1, out: "timeout: ${out}".toString()]
                    }
                    out.append(err)
                    return [exit: proc.exitValue(), out: out.toString()]
                } catch (Exception e) { return [exit: -1, out: e.message] }
            }

            // Clean up stale state
            def lockFile = new File(git_root, ".git/index.lock")
            if (lockFile.exists()) lockFile.delete()

            // Trust all repos regardless of filesystem ownership (cross-OS)
            runGit(["git", "config", "--global", "safe.directory", "*"], 5)

            runGit(["git", "rebase", "--abort"], 2)
            new File(git_root, ".git/rebase-merge").with { if (exists()) deleteDir() }
            new File(git_root, ".git/rebase-apply").with { if (exists()) deleteDir() }

            // Scoped paths: {project}_l1, {project}_l2, .bin
            def syncPaths = []
            def bin_full = new File(output_root_full, ".bin")
            if (bin_full.exists()) {
                syncPaths << git_root.toPath().relativize(bin_full.toPath()).toString().replace('\\', '/')
            }
            def l1_full = new File(output_root_full, "${params.project_name}_l1")
            if (l1_full.exists()) {
                syncPaths << git_root.toPath().relativize(l1_full.toPath()).toString().replace('\\', '/')
            }
            def l2_folder_name = params.l2_folder ?: "${params.project_name}_l2"
            def l2_full = new File(output_root_full, l2_folder_name)
            if (l2_full.exists()) {
                syncPaths << git_root.toPath().relativize(l2_full.toPath()).toString().replace('\\', '/')
            }
            if (!syncPaths) return

            def addResult = runGit(["git", "add", "-A"] + syncPaths, 120)
            if (addResult.exit != 0) {
                try { pipeline_log.append("[${ts}] [finalSync] git add failed: ${addResult.out}\n") } catch (Exception ignored) {}
                return
            }
            def st = runGit(["git", "status", "--porcelain", "--cached"], 15)
            if (!st.out?.trim()) {
                try { pipeline_log.append("[${ts}] [finalSync] Nothing to commit\n") } catch (Exception ignored) {}
                return
            }
            runGit(["git", "commit", "-m", "autosync: final sync (pipeline complete)"], 30)
            def push = runGit(["git", "push"], 600)
            if (push.exit != 0) {
                def pull = runGit(["git", "pull", "--rebase"], 120)
                if (pull.exit != 0) {
                    runGit(["git", "rebase", "--abort"], 2)
                }
                runGit(["git", "push"], 600)
            }
            try { pipeline_log.append("[${ts}] [finalSync] Complete\n") } catch (Exception ignored) {}
        } finally {
            git_lock.unlock()
        }
    } catch (Exception e) {
        def ts = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
        try { pipeline_log.append("[${ts}] [ERROR] finalSync failed: ${e.message}\n") } catch (Exception ignored) {}
    }
}

// Generic IOInterface: exe script input params
// Language-agnostic CLI wrapper for any executable (Python, Java, Rust, etc.)
process IOInterface {
    tag "${input}"              // Tag with input filename for trace file
    
    input:
        val env_exe             // Executable path (python, java, rust binary, etc.)
        val script              // Script path relative to workflow.launchDir
        path input              // Input file(s) - automatically staged by Nextflow
        val extraParams         // Additional arguments

    output:
        path "*.{fif,parquet}", optional: true

    script:
    // Shell-escape single quotes by replacing ' with '\''
    def escapeArg = { arg -> arg.toString().replace("'", "'\\''") }
    
    // Format inputs: convert to quoted shell arguments
    def inputArgs = input instanceof Collection 
        ? input.collect { "'${escapeArg(it)}'" }.join(' ')
        : "'${escapeArg(input)}'"
    
    // Format additional args: smart split that preserves bracketed/braced structures
    def extraArgs = ""
    def isGroupLog = false
    def isTerminal = false
    def isResult = false
    def isTable = false
    if (extraParams && extraParams.toString().trim() != "") {
        def paramStr = extraParams.toString().trim()
        
        // Parse arguments respecting brackets and braces
        def args = []
        def currentArg = new StringBuilder()
        def depth = 0
        def inQuote = false
        
        for (int i = 0; i < paramStr.length(); i++) {
            def c = paramStr.charAt(i)
            
            if (c == '"' as char || c == "'" as char) {
                inQuote = !inQuote
                currentArg.append(c)
            } else if (!inQuote) {
                if (c == '[' as char || c == '{' as char) {
                    depth++
                    currentArg.append(c)
                } else if (c == ']' as char || c == '}' as char) {
                    depth--
                    currentArg.append(c)
                } else if (c == ' ' as char && depth == 0) {
                    if (currentArg.length() > 0) {
                        args.add(currentArg.toString())
                        currentArg = new StringBuilder()
                    }
                } else {
                    currentArg.append(c)
                }
            } else {
                currentArg.append(c)
            }
        }
        
        if (currentArg.length() > 0) {
            args.add(currentArg.toString())
        }
        
        // Strip group_log token — signals group-level context; not forwarded to the underlying script
        isGroupLog = args.remove('group_log')
        // Strip terminal token — marks this process as a terminal branch for finalization.
        // Terminal processes always emit output (even on failure) so finalization is never blocked.
        isTerminal = args.remove('terminal')
        // Strip result token — publishes output to results/ instead of plots/.
        // Used by result_collector to create a clean-named curated output folder.
        isResult = args.remove('result')
        // Strip table token — marks this process as a data-table export.
        // Table processes publish to tables/ instead of results/ or plots/,
        // and are not registered in the HTML archive (they are raw data files).
        isTable = args.remove('table')
        extraArgs = args.collect { "'${escapeArg(it)}'" }.join(' ')
    }
    
    // L1/L2 naming follows FSL/SPM convention:
    //   l1 = first-level (per-participant), l2 = second-level (group/cross-participant)
    // Group folder is derived as {project_name}_l2 — created on-demand when a group-level process runs.
    def groupFolderName = "${params.project_name}_l2"
    def groupDir        = "${workflow.launchDir}/${params.output_dir}/${groupFolderName}"
    def groupLogFile    = "${groupDir}/${groupFolderName}.log.parquet"

    // Resolve log and context-directory paths:
    // Groovy params are interpolated now; \${PARTICIPANT_ID} stays as a bash variable reference
    // Logs are written directly as .log.parquet (live, readable by the HTML viewer during the run).
    def logFilePath    = isGroupLog
        ? groupLogFile
        : "${workflow.launchDir}/${params.output_dir}/${params.project_name}_l1/\${PARTICIPANT_ID}/\${PARTICIPANT_ID}.log.parquet"
    def contextDirPath = isGroupLog
        ? groupDir
        : "${workflow.launchDir}/${params.output_dir}/${params.project_name}_l1/\${PARTICIPANT_ID}"
    // plots/ subfolder mirrors the structure of participant folders (log.parquet sibling to plots/)
    // When the result token is present, output goes to results/ instead (curated clean-named files).
    // When the table token is present, output goes to tables/ (raw epoch data for Spyder/MATLAB inspection).
    def publishFolder = isTable ? 'tables' : (isResult ? 'results' : 'plots')
    def contextPlotDir = isGroupLog
        ? "${groupDir}/${publishFolder}"
        : "${workflow.launchDir}/${params.output_dir}/${params.project_name}_l1/\${PARTICIPANT_ID}/${publishFolder}"
    def contextCondition = isGroupLog ? "true" : "[ -n \"\$PARTICIPANT_ID\" ]"
    // Path to the live parquet log writer utility
    def logWriter = "${workflow.launchDir}/${params.toolbox_dir}/bin/log_to_parquet.py"

    // Extract script name for logging
    def scriptName = script.toString().tokenize('/').last().replace('.py', '')
    
    """
    #!/bin/bash
    
    # Extract participant ID from input filename (pattern like EV_002_*)
    INPUT_FILE=\$(basename "${inputArgs}" | sed "s/'//g")
    PARTICIPANT_ID=\$(echo "\$INPUT_FILE" | grep -oE '^[A-Za-z]+_[0-9]+' | head -1)
    
    # Logs are written directly as .log.parquet so the HTML viewer can read them live
    # during the run — no text .log file, no end-of-run conversion.
    LOG_FILE="${logFilePath}"
    CONTEXT_DIR="${contextDirPath}"
    CONTEXT_PLOT_DIR="${contextPlotDir}"

    if ${contextCondition}; then
        mkdir -p "\$CONTEXT_DIR" "\$CONTEXT_PLOT_DIR"

        # Initialize log on first use (write parquet header)
        if [ ! -f "\$LOG_FILE" ]; then
            INIT_TMP=\$(mktemp)
            printf "=== ${params.project_name} log: %s ===\\nWorkflow: ${workflow.projectDir}\\nSession:  ${workflow.sessionId}\\nOutput:   %s\\n\\n" \
                "\$(date '+%Y-%m-%d %H:%M:%S')" "\$CONTEXT_DIR" > "\$INIT_TMP"
            ${env_exe} -u "${logWriter}" "\$LOG_FILE" "\$INIT_TMP"
            rm -f "\$INIT_TMP"
        fi

        # --- Correction Override ---
        # When manual corrections exist for this step, they replace the
        # computed output entirely and the script is skipped.
        # Convention:  <participant>/corrections/<scriptName>/<file>.parquet
        # The notebook writes validated corrections there; the gate copies
        # them into the work directory so downstream channels receive the
        # corrected data and normal publishing picks them up.
        CORRECTION_DIR="\$CONTEXT_DIR/corrections/${scriptName}"
        HAS_CORRECTIONS=false
        if [ -d "\$CORRECTION_DIR" ]; then
            shopt -s nullglob
            _CORR_FILES=("\$CORRECTION_DIR"/*.parquet)
            shopt -u nullglob
            if [ \${#_CORR_FILES[@]} -gt 0 ]; then
                HAS_CORRECTIONS=true
                for _cf in "\${_CORR_FILES[@]}"; do cp "\$_cf" .; done
                _CORR_TMP=\$(mktemp)
                printf "%s [CORRECTION] Applied %d override(s) from corrections/${scriptName}/, skipping script\\n" \
                    "\$(date '+%Y-%m-%d %H:%M:%S')" \${#_CORR_FILES[@]} > "\$_CORR_TMP"
                ${env_exe} -u "${logWriter}" "\$LOG_FILE" "\$_CORR_TMP"
                rm -f "\$_CORR_TMP"
            fi
        fi

        if [ "\$HAS_CORRECTIONS" != "true" ]; then
        TEMP_OUT=\$(mktemp)
        export VIS_LABEL_MAP='${params.vis_label_map}'
        ${env_exe} -u "${workflow.launchDir}/${script}" ${inputArgs} ${extraArgs} 2>&1 | tee "\$TEMP_OUT"
        EXIT_CODE=\${PIPESTATUS[0]}

        # Prepend timestamps to each output line, then append to .log.parquet live
        STAMPED_TMP=\$(mktemp)
        while IFS= read -r line; do
            printf "%s %s\\n" "\$(date '+%Y-%m-%d %H:%M:%S')" "\$line"
        done < "\$TEMP_OUT" > "\$STAMPED_TMP"
        ${env_exe} -u "${logWriter}" "\$LOG_FILE" "\$STAMPED_TMP"
        rm -f "\$TEMP_OUT" "\$STAMPED_TMP"

        if [ \$EXIT_CODE -ne 0 ]; then
            ERR_TMP=\$(mktemp)
            printf "\\n%s [ERROR] ${scriptName} exit code %d\\n" "\$(date '+%Y-%m-%d %H:%M:%S')" \$EXIT_CODE > "\$ERR_TMP"
            ${env_exe} -u "${logWriter}" "\$LOG_FILE" "\$ERR_TMP"
            rm -f "\$ERR_TMP"
            ${isTerminal ? "# Terminal process: emit sentinel so finalization is never blocked\n            ${env_exe} -c \"import polars as pl; pl.DataFrame({'_sentinel': [True], '_error': [True]}).write_parquet('\${PARTICIPANT_ID}_sentinel_failed.parquet', compression='snappy')\"\n            exit 0" : 'exit $EXIT_CODE'}
        fi
        fi  # end HAS_CORRECTIONS check

        # Publish output parquets to the results plots/ folder and register
        # them in the HTML archive.  Only .parquet files are staged — other
        # formats (.fif, etc.) are calculation intermediates and stay in the
        # Nextflow work directory (which is gitignored).
        #
        # Folder convention: when a module splits output into a subdirectory
        # (e.g. *_bs/, *_psd/), the root-level .parquet is just a Nextflow
        # signal file.  In that case we publish the per-file parquets from
        # the subfolder instead.
        mkdir -p "${workflow.launchDir}/${params.output_dir}"
        PROCEDURE_FOLDER="\$(cd "${workflow.launchDir}/${params.output_dir}" && pwd)"
        PROJECT_NAME="${params.project_name}"

        IS_RESULT=${isResult ? '"true"' : '"false"'}
        IS_TABLE=${isTable ? '"true"' : '"false"'}
        IS_TERMINAL=${isTerminal ? '"true"' : '"false"'}
        IS_GROUP=${isGroupLog ? '"true"' : '"false"'}
        STAGED_BASENAMES="|"
        for _IN in ${inputArgs}; do
            _BASE="\$(basename "\$_IN")"
            STAGED_BASENAMES="\${STAGED_BASENAMES}\${_BASE}|"
        done
        _publish_parquet() {
            local FILE="\$1"
            local BASENAME=\$(basename "\$FILE")
            # For result and table processes, strip _result suffix so published file has clean name.
            # Only add .parquet back when _result.parquet was actually present; otherwise keep name as-is
            # to avoid producing double-extension files (e.g. foo.parquet.parquet).
            if [ "\$IS_RESULT" = "true" ] || [ "\$IS_TABLE" = "true" ]; then
                case "\$BASENAME" in
                    *_result.parquet) BASENAME="\${BASENAME%_result.parquet}.parquet" ;;
                esac
                # Copy file to results/ or tables/ folder for curated outputs
                mkdir -p "\$CONTEXT_PLOT_DIR"
                cp "\$FILE" "\$CONTEXT_PLOT_DIR/\$BASENAME" 2>/dev/null || true
            fi
            local PREFIX=\${BASENAME%.parquet}

            # Auto-table: terminal L1 analysis processes that emit raw epoch data
            # (parquets without a plot_type column) are automatically copied to
            # tables/ so every project gets per-participant data for Spyder/MATLAB
            # without any explicit pipeline wiring.
            # Skipped for: result_collector outputs (IS_RESULT), explicit table
            # exports (IS_TABLE), group-level processes (IS_GROUP), and any parquet
            # that already has a plot_type column (plot-ready summary, not raw data).
            if [ "\$IS_TERMINAL" = "true" ] && [ "\$IS_RESULT" != "true" ] && [ "\$IS_TABLE" != "true" ] && [ "\$IS_GROUP" != "true" ]; then
                _IS_SENTINEL=\$(${env_exe} -c "
import polars as pl, sys
try:
    df = pl.read_parquet(sys.argv[1])
    print('1' if '_sentinel' in df.columns else '0')
except Exception:
    print('0')
" "\$FILE" 2>/dev/null || echo '0')
                if [ "\$_IS_SENTINEL" = "0" ]; then
                    mkdir -p "\$CONTEXT_DIR/tables"
                    cp "\$FILE" "\$CONTEXT_DIR/tables/\$BASENAME" 2>/dev/null || true
                fi
            fi

            # Register all published parquets in the HTML archive, including raw tables.
            local REG_TMP=\$(mktemp)
            ${env_exe} -u "${workflow.launchDir}/${params.interactive_plotter_script}" "\$FILE" "\$PROCEDURE_FOLDER" "\$PREFIX" "\$PROJECT_NAME" "\$CONTEXT_PLOT_DIR" 2>&1 | \
                while IFS= read -r line; do printf "%s %s\\n" "\$(date '+%Y-%m-%d %H:%M:%S')" "\$line"; done > "\$REG_TMP"
            ${env_exe} -u "${logWriter}" "\$LOG_FILE" "\$REG_TMP"
            rm -f "\$REG_TMP"
        }

        # Detect whether any subfolder contains parquet files.
        HAS_SUBFOLDER=false
        for DIR in */; do
            [ -d "\$DIR" ] || continue
            for SUB in "\$DIR"*.parquet; do
                if [ -f "\$SUB" ]; then HAS_SUBFOLDER=true; break 2; fi
            done
        done

        if [ "\$HAS_SUBFOLDER" = "true" ]; then
            # Publish parquets from subfolders (the real data).
            for DIR in */; do
                [ -d "\$DIR" ] || continue
                for SUB in "\$DIR"*.parquet; do
                    [ -f "\$SUB" ] || continue
                    _publish_parquet "\$SUB"
                done
            done
        else
            # No subfolders — publish root-level parquets.
            for OUT_FILE in *.parquet; do
                [ -f "\$OUT_FILE" ] || continue
                # For result and table processes, skip staged inputs — only publish _result outputs.
                if [ "\$IS_RESULT" = "true" ] || [ "\$IS_TABLE" = "true" ]; then
                    case "\$OUT_FILE" in
                        *_result.parquet) ;;
                        *)
                            # Group-level result processes often emit canonical names
                            # (e.g., *_anova.parquet, *_summary.parquet) without _result suffix.
                            # Publish only those curated result files to results/; raw combined
                            # data tables (e.g. *_binned.parquet) are skipped here — they are
                            # not useful in results/ and are already available in per-participant
                            # tables/ folders.
                            if [ "\$IS_GROUP" = "true" ] && [ "\$IS_RESULT" = "true" ]; then
                                case "\$STAGED_BASENAMES" in
                                    *"|\$OUT_FILE|"*) continue ;;
                                    *) ;;
                                esac
                            else
                                continue
                            fi
                        ;;
                    esac
                fi
                _publish_parquet "\$OUT_FILE"
            done
        fi

        exit 0
    else
        ${env_exe} -u "${workflow.launchDir}/${script}" ${inputArgs} ${extraArgs}
    fi
    """
}
