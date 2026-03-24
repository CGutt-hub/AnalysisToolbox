import java.util.concurrent.locks.ReentrantLock

// Shared git lock for sequential git operations across participants
@groovy.transform.Field
static java.util.concurrent.locks.ReentrantLock git_lock = new java.util.concurrent.locks.ReentrantLock()

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
                // Set git user config in repository (not global to avoid permission issues)
                ["git", "config", "user.email", params.git_user_email].execute(null, git_root).waitFor()
                ["git", "config", "user.name",  params.git_user_name ].execute(null, git_root).waitFor()
                git_configured = true
                
                // Log to ${params.project_name}.log instead of terminal
                def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}.log")
                def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                pipeline_log.append("[${timestamp}] [workflow] Git user configured: ${params.git_user_name} <${params.git_user_email}>\n")
            }
        } catch (Exception e) {
            def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}.log")
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
        def l1_path    = new File(output_path, "${params.project_name}_l1")
        def output_dirs = l1_path.exists() ? l1_path.list() as Set : [] as Set
        def new_participants = input_path.list().findAll { it.matches(regex_pattern) }.findAll { !(it in output_dirs) }
        
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
            l2_dir.mkdirs()
            l2_plots_dir.mkdirs()
            // l2 log is a .log.parquet written live by IOInterface bash block — no text file needed here
        }

        // Clean up any leftover .finalized marker files from old pipeline runs
        output_path.eachDirRecurse { dir ->
            def marker = new File(dir, ".finalized")
            if (marker.exists()) marker.delete()
        }

        def watched_participants = Channel
            .watchPath("${workflow.launchDir}/${input_dir}/*", 'create,modify')
            .map { path -> path.getName() }
            .filter { it.matches(regex_pattern) }
            .unique()

        def all_participants = Channel.fromList(new_participants).concat(watched_participants)
            .filter { pid ->
                def safe_id = pid.replaceAll('\r', '').trim().replaceAll('[^A-Za-z0-9._-]', '_')
                !new File("${workflow.launchDir}/${output_dir}/${params.project_name}_l1/${safe_id}").exists()
            }

        participant_context = all_participants.map { pid ->
            def safe_id = pid.replaceAll('\r', '').trim().replaceAll('[^A-Za-z0-9._-]', '_')
            // Per-participant subfolder: {output_dir}/{project_name}_l1/{safe_id}/
            def l1_dir = new File("${workflow.launchDir}/${output_dir}/${params.project_name}_l1")
            l1_dir.mkdirs()
            def participant_dir = new File(l1_dir, safe_id)
            participant_dir.mkdirs()
            
            // Log init happens in the IOInterface bash block on first task run (writes .log.parquet live).
            // Just append to the global pipeline log here.
            def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
            def global_pipeline_log = new File(new File("${workflow.launchDir}/${params.output_dir}", ".bin"), "${params.project_name}.log")
            global_pipeline_log.append("=== ${safe_id} initialized: ${timestamp} ===\nOutput: ${participant_dir}\n\n")
            
            // HTML lives in .bin/ subfolder at the results root (shared across participants)
            def output_root = new File("${workflow.launchDir}/${output_dir}")
            def bin_dir_html = new File(output_root, ".bin")
            bin_dir_html.mkdirs()
            def html_file = new File(bin_dir_html, "${params.project_name}_results.html")
            def serve_file = new File(bin_dir_html, "${params.project_name}_results_serve.py")
            if (!html_file.exists() || !serve_file.exists()) {
                def init_cmd = [params.python_exe, '-u',
                    "${workflow.launchDir}/${params.toolbox_dir}/utils/interactive_plotter.py",
                    'init', html_file.absolutePath]
                def proc = init_cmd.execute()
                proc.waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
                if (proc.exitValue() != 0) {
                    new File(new File("${workflow.launchDir}/${params.output_dir}", ".bin"), "${params.project_name}.log").append(
                        "[${new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())}] [workflow] Warning: Could not initialize HTML archive: ${proc.text}\n"
                    )
                }
            }
            
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
                "${workflow.launchDir}/${params.toolbox_dir}/utils/log_to_parquet.py",
                log_file.absolutePath, '--text', finalization_text]
            append_cmd.execute().waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
        } catch (Exception e) { /* non-critical */ }
    }

    // Write finalization to central pipeline log (inside .bin/)
    def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}.log")
    try {
        pipeline_log.parentFile?.mkdirs()
        if (!pipeline_log.exists()) pipeline_log.text = ""
    } catch (Exception e) { /* NAS/SMB can transiently fail */ }

    // Register the live .log.parquet in the interactive HTML archive (meta.json)
    // add_log_to_archive detects .log.parquet and uses the file at its existing location.
    def procedure_html = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}_results.html")
    if (procedure_html.exists() && log_file.exists()) {
        try {
            def add_log_cmd = [params.python_exe, '-u',
                "${workflow.launchDir}/${params.toolbox_dir}/utils/interactive_plotter.py",
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

        def runGit = { cmd, timeout = 10 ->
            try {
                def env = ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
                def proc = cmd.execute(env, git_root)
                def out = new StringBuilder()
                def err = new StringBuilder()
                // Run waitForProcessOutput in a thread so we can enforce a timeout
                // without leaving orphaned TextDumper threads that throw on stream close.
                def reader = Thread.start { proc.waitForProcessOutput(out, err) }
                reader.join(timeout * 1000L)
                if (reader.isAlive()) {
                    proc.destroy()
                    reader.join(2000L)  // let reader notice the closed stream
                    out.append(err)
                    return [exit: -1, out: "timeout: ${out}".toString()]
                }
                out.append(err)
                return [exit: proc.exitValue(), out: out.toString()]
            } catch (Exception e) {
                return [exit: -1, out: e.message]
            }
        }

        def cleanupRebase = {
            runGit(["git", "rebase", "--abort"], 2)
            new File(git_root, ".git/rebase-merge").with { if (exists()) deleteDir() }
            new File(git_root, ".git/rebase-apply").with { if (exists()) deleteDir() }
            new File(git_root, ".git/index.lock").with { if (exists()) delete() }
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

        // Commit 1: participant subfolder + shared HTML + meta.json + {project}_l2/ (if it exists)
        def addPaths = [relative_path]
        if (html_path) addPaths << html_path
        if (meta_path) addPaths << meta_path
        def group_full = new File(output_root_full, "${params.project_name}_l2")
        if (group_full.exists()) {
            def group_path = git_root.toPath().relativize(group_full.toPath()).toString().replace('\\', '/')
            addPaths << group_path
        }

        // git add can be slow on NAS/network mounts with many large parquet files — use 120 s.
        def add = runGit(["git", "add", "-A"] + addPaths, 120)
        if (add.exit != 0) {
            runGit(["git", "reset", "HEAD"], 10)
            logSync("Git add failed", add.out)
        } else {
            def status_participant = runGit(["git", "status", "--porcelain", "--cached"] + addPaths, 15)
            def hasParticipantChanges = status_participant.out?.trim()
            if (hasParticipantChanges) {
                def commit = runGit(["git", "commit", "-m", "autosync: ${pid} completed"], 30)
                if (commit.exit == 0) {
                    def pull = runGit(["git", "pull", "--rebase", "--autostash"], 600)
                    if (pull.exit != 0) {
                        cleanupRebase()
                        runGit(["git", "reset", "--hard", "HEAD"], 10)
                        logSync("Git pull failed", pull.out)
                    } else {
                        def push = runGit(["git", "push"], 900)
                        logSync(push.exit == 0 ? "Participant synced" : "Push failed (committed locally)", push.out)
                    }
                } else {
                    logSync("Commit failed", commit.out)
                }
            } else {
                logSync("No participant changes")
            }
        }

        // Commit 2: convert pipeline log -> parquet, delete text file, sync parquet
        def pipeline_log_parquet      = new File(pipeline_log.parentFile, "${params.project_name}.log.parquet")
        def pipeline_log_parquet_path = git_root.toPath().relativize(pipeline_log_parquet.toPath()).toString().replace('\\', '/')
        if (procedure_html.exists() && pipeline_log.exists()) {
            try {
                def add_global_cmd = [params.python_exe, '-u',
                    "${workflow.launchDir}/${params.toolbox_dir}/utils/interactive_plotter.py",
                    'add-log', procedure_html.absolutePath, 'global', pipeline_log.absolutePath, "${params.project_name}.log"]
                def proc2 = add_global_cmd.execute()
                def finished = proc2.waitFor(60, java.util.concurrent.TimeUnit.SECONDS)
                if (finished && proc2.exitValue() == 0) pipeline_log.delete()
            } catch (Exception e) { /* non-critical */ }
        }
        def analysis_dir      = new File("${workflow.launchDir}").getAbsoluteFile()
        def analysis_dir_path = git_root.toPath().relativize(analysis_dir.toPath()).toString().replace('\\', '/')
        def logSyncPath       = pipeline_log_parquet.exists() ? pipeline_log_parquet_path : pipeline_log_path
        def addLog = runGit(["git", "add", "-A", logSyncPath, analysis_dir_path], 120)
        if (addLog.exit == 0) {
            def commitLog = runGit(["git", "commit", "-m", "${params.project_name}.log: ${pid} complete"], 30)
            if (commitLog.exit == 0) {
                runGit(["git", "pull", "--rebase"], 600)
                runGit(["git", "push"], 900)
            }
        }
    } finally {
        git_lock.unlock()
    }
}

// Separate finalization workflow: logging + git sync
workflow finalize_participant {
    take:
        terminal_plots
        participant_context
    
    main:
        def terminal_outputs = Channel.empty()
        def terminal_count = terminal_plots.size()
        terminal_plots.each { ch -> terminal_outputs = terminal_outputs.mix(ch) }
        
        def finalizedPids = Collections.synchronizedSet(new HashSet<String>())

        terminal_outputs
            .map { file -> 
                def pid = file.baseName.toString().split('_')[0..1].join('_')
                [pid, file]
            }
            .groupTuple(size: terminal_count, remainder: true)
            .join(participant_context)
            .subscribe { pid, files, folder ->
                finalizeParticipant(pid, files, folder, finalizedPids)
            }
}

// Finalize the group-level (l2) folder: append completion entry to EV_l2.log.parquet,
// register it in the HTML archive, and commit + push the entire EV_l2 folder.
// Call this after all l2 processes complete (use .collect() on l2 outputs in the pipeline).
def finalizeL2(files) {
    def timestamp   = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
    def l2_name     = "${params.project_name}_l2"
    def l2_dir      = new File("${workflow.launchDir}/${params.output_dir}/${l2_name}")
    def log_file    = new File(l2_dir, "${l2_name}.log.parquet")
    def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}.log")

    def finalization_text = """
=== Group-level analysis (${l2_name}) completed: ${timestamp} ===
Files produced: ${files.size()}
Session: ${workflow.sessionId}
=== ${l2_name} finalized: ${timestamp} ===

"""
    if (log_file.exists()) {
        try {
            def append_cmd = [params.python_exe, '-u',
                "${workflow.launchDir}/${params.toolbox_dir}/utils/log_to_parquet.py",
                log_file.absolutePath, '--text', finalization_text]
            append_cmd.execute().waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
        } catch (Exception e) { /* non-critical */ }
    }

    // Register the l2 log.parquet in the HTML archive
    def procedure_html = new File("${workflow.launchDir}/${params.output_dir}/.bin", "${params.project_name}_results.html")
    if (procedure_html.exists() && log_file.exists()) {
        try {
            def add_log_cmd = [params.python_exe, '-u',
                "${workflow.launchDir}/${params.toolbox_dir}/utils/interactive_plotter.py",
                'add-log', procedure_html.absolutePath, l2_name, log_file.absolutePath, 'Group Log']
            add_log_cmd.execute().waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
        } catch (Exception e) { /* non-critical */ }
    }

    try {
        pipeline_log.parentFile?.mkdirs()
        if (!pipeline_log.exists()) pipeline_log.text = ""
        pipeline_log.append("\n=== ${l2_name} finalized: ${timestamp} ===\nFiles: ${files.size()}\n\n")
    } catch (Exception e) { /* .bin/ may not exist yet on NAS */ }

    git_lock.lock()
    try {
        def l2_full = l2_dir.getAbsoluteFile()
        def git_root = l2_full
        while (git_root != null && !new File(git_root, ".git").exists()) {
            git_root = git_root.getParentFile()
        }
        if (!git_root) return

        def l2_path  = git_root.toPath().relativize(l2_full.toPath()).toString().replace('\\', '/')
        def bin_full = new File("${workflow.launchDir}/${params.output_dir}/.bin").getAbsoluteFile()
        def meta_full = new File(bin_full, "${params.project_name}_meta.json")
        def html_full = new File(bin_full, "${params.project_name}_results.html")

        def runGit = { cmd, timeout = 10 ->
            try {
                def env = ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
                def proc = cmd.execute(env, git_root)
                def out = new StringBuilder(); def err = new StringBuilder()
                def reader = Thread.start { proc.waitForProcessOutput(out, err) }
                reader.join(timeout * 1000L)
                if (reader.isAlive()) { proc.destroy(); reader.join(2000L); return [exit: -1, out: "timeout"] }
                out.append(err)
                return [exit: proc.exitValue(), out: out.toString()]
            } catch (Exception e) { return [exit: -1, out: e.message] }
        }

        def addPaths = [l2_path]
        if (meta_full.exists()) addPaths << git_root.toPath().relativize(meta_full.toPath()).toString().replace('\\', '/')
        if (html_full.exists()) addPaths << git_root.toPath().relativize(html_full.toPath()).toString().replace('\\', '/')

        def logMsg = { msg -> try { pipeline_log.append(msg) } catch (Exception ignored) {} }

        // Clean up stale git state
        new File(git_root, ".git/index.lock").with { if (exists()) delete() }
        new File(git_root, ".git/rebase-merge").with { if (exists()) deleteDir() }
        new File(git_root, ".git/rebase-apply").with { if (exists()) deleteDir() }

        def add = runGit(["git", "add", "-A"] + addPaths, 120)
        if (add.exit == 0) {
            def status = runGit(["git", "status", "--porcelain", "--cached"] + addPaths, 15)
            if (status.out?.trim()) {
                def commit = runGit(["git", "commit", "-m", "autosync: ${l2_name} completed"], 30)
                if (commit.exit != 0) {
                    logMsg("Git sync ${l2_name}: commit failed\n${commit.out}\n")
                }
            }
        } else {
            logMsg("Git sync ${l2_name}: git add failed\n${add.out}\n")
        }

        // Push L2 results (participant commits already pushed individually)
        def pull = runGit(["git", "pull", "--rebase", "--autostash"], 600)
        if (pull.exit != 0) {
            new File(git_root, ".git/index.lock").with { if (exists()) delete() }
            runGit(["git", "rebase", "--abort"], 5)
            logMsg("Git sync: pull --rebase failed, retrying with merge\n${pull.out}\n")
            pull = runGit(["git", "pull"], 600)
        }
        def push = runGit(["git", "push"], 900)
        logMsg("Git sync L2 push: ${push.exit == 0 ? 'success' : 'failed'}\n${push.out}\n")
    } finally {
        git_lock.unlock()
    }
}

// Finalization workflow for group-level (l2) outputs.
// Pass all l2 process output channels mixed together; .collect() waits for all of them.
workflow finalize_l2 {
    take:
        l2_outputs

    main:
        l2_outputs
            .collect()
            .subscribe { files ->
                finalizeL2(files)
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
    def contextPlotDir = isGroupLog
        ? "${groupDir}/plots"
        : "${workflow.launchDir}/${params.output_dir}/${params.project_name}_l1/\${PARTICIPANT_ID}/plots"
    def contextCondition = isGroupLog ? "true" : "[ -n \"\$PARTICIPANT_ID\" ]"
    // Path to the live parquet log writer utility
    def logWriter = "${workflow.launchDir}/${params.toolbox_dir}/utils/log_to_parquet.py"

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
            exit \$EXIT_CODE
        fi

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

        _publish_parquet() {
            local FILE="\$1"
            local BASENAME=\$(basename "\$FILE")
            local PREFIX=\${BASENAME%.parquet}
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
                _publish_parquet "\$OUT_FILE"
            done
        fi

        exit 0
    else
        ${env_exe} -u "${workflow.launchDir}/${script}" ${inputArgs} ${extraArgs}
    fi
    """
}
