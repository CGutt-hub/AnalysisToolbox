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
                def email_cmd = ["git", "config", "user.email", params.git_user_email].execute(null, git_root)
                email_cmd.waitFor()
                def name_cmd = ["git", "config", "user.name", params.git_user_name].execute(null, git_root)
                name_cmd.waitFor()
                
                // Verify config was set
                def verify_email = ["git", "config", "user.email"].execute(null, git_root)
                verify_email.waitFor()
                def verify_name = ["git", "config", "user.name"].execute(null, git_root)
                verify_name.waitFor()
                
                git_configured = true
                
                // Log to pipeline.log instead of terminal
                def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}", "pipeline.log")
                def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                pipeline_log.append("[${timestamp}] [workflow] Git user configured: ${params.git_user_name} <${params.git_user_email}>\n")
            }
        } catch (Exception e) {
            def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}", "pipeline.log")
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
        // Discover already-processed participants by checking per-participant output subfolders
        def output_dirs = output_path.exists() ? output_path.list() as Set : [] as Set
        def new_participants = input_path.list().findAll { it.matches(regex_pattern) }.findAll { !(it in output_dirs) }
        
        // Create pipeline log file
        def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}", "pipeline.log")
        if (!pipeline_log.exists()) {
            pipeline_log.text = ""
        }

        // Clean up any leftover .finalized marker files from old pipeline runs
        output_path.eachDirRecurse { dir ->
            def marker = new File(dir, ".finalized")
            if (marker.exists()) marker.delete()
        }

        // Delete stale old-named HTML files (e.g. *_interactive.html) alongside the new *_results.html.
        // These are leftovers from before the rename and will confuse serve_html.ps1.
        if (output_path.exists()) {
            output_path.listFiles()?.each { f ->
                if (f.name.endsWith('_interactive.html')) {
                    f.delete()
                    new File("${workflow.launchDir}/${params.output_dir}", "pipeline.log").append(
                        "[${new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())}] [workflow] Deleted stale HTML: ${f.name}\n"
                    )
                }
            }
        }
        // Same cleanup in the launch dir itself (EV_analysis/)
        new File(workflow.launchDir.toString()).listFiles()?.each { f ->
            if (f.name.endsWith('_interactive.html')) {
                f.delete()
            }
        }

        def watched_participants = Channel
            .watchPath("${workflow.launchDir}/${input_dir}/*", 'create,modify')
            .map { path -> path.getName() }
            .filter { it.matches(regex_pattern) }
            .unique()

        def all_participants = Channel.fromList(new_participants).concat(watched_participants)
            .filter { pid ->
                def safe_id = pid.replaceAll('\r', '').trim().replaceAll('[^A-Za-z0-9._-]', '_')
                !new File("${workflow.launchDir}/${output_dir}/${safe_id}").exists()
            }

        participant_context = all_participants.map { pid ->
            def safe_id = pid.replaceAll('\r', '').trim().replaceAll('[^A-Za-z0-9._-]', '_')
            // Per-participant subfolder: EV_results/EV_002/
            // Contains: plots/ (parquet sidecars), *.pdf (exports)
            def participant_dir = new File("${workflow.launchDir}/${output_dir}/${safe_id}")
            participant_dir.mkdirs()
            
            def log_file = new File(participant_dir, "${safe_id}_pipeline.log")
            def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
            
            if (!log_file.exists()) {
                log_file.text = "=== ${safe_id} initialized: ${timestamp} ===\n"
                log_file.append("Workflow: ${workflow.projectDir}\n")
                log_file.append("Session: ${workflow.sessionId}\n")
                log_file.append("Launch dir: ${workflow.launchDir}\n")
                log_file.append("Output: ${participant_dir}\n")
                log_file.append("\n=== Analysis started for ${safe_id}: ${timestamp} ===\n\n")
                
                def global_pipeline_log = new File("${workflow.launchDir}/${params.output_dir}", "pipeline.log")
                global_pipeline_log.append("=== ${safe_id} initialized: ${timestamp} ===\n")
                global_pipeline_log.append("Workflow: ${workflow.projectDir}\n")
                global_pipeline_log.append("Session: ${workflow.sessionId}\n")
                global_pipeline_log.append("Launch dir: ${workflow.launchDir}\n")
                global_pipeline_log.append("Output: ${participant_dir}\n")
                global_pipeline_log.append("\n=== Analysis started for ${safe_id}: ${timestamp} ===\n\n")
            }
            
            // HTML lives at EV_results/ root (shared across participants)
            def output_root = new File("${workflow.launchDir}/${output_dir}")
            def html_file = new File(output_root, "${params.project_name}_results.html")
            if (!html_file.exists()) {
                def init_cmd = [params.python_exe, '-u',
                    "${workflow.launchDir}/${params.toolbox_dir}/utils/interactive_plotter.py",
                    'init', html_file.absolutePath]
                def proc = init_cmd.execute()
                proc.waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
                if (proc.exitValue() != 0) {
                    new File("${workflow.launchDir}/${params.output_dir}", "pipeline.log").append(
                        "[${new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())}] [workflow] Warning: Could not initialize HTML archive: ${proc.text}\n"
                    )
                }
            }
            
            def folder = "${output_dir}/${safe_id}"
            [pid, folder]
        }

    emit:
        participant_context
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
            .groupTuple(size: terminal_count)
            .join(participant_context)
            .subscribe { pid, files, folder ->
                // Deduplicate in-memory (no marker file needed)
                if (!finalizedPids.add(pid)) {
                    return
                }
                
                Thread.sleep(2000)
                
                def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                def log_file = new File("${workflow.launchDir}/${folder}/${pid}_pipeline.log")
                def start_time = log_file.exists() ? new Date(log_file.lastModified()) : new Date()
                def duration = (new Date().time - start_time.time) / 1000
                
                // Write finalization to participant log
                log_file?.append("\n=== Analysis completed for ${pid}: ${timestamp} ===\n")
                log_file?.append("Terminal modules completed: ${files.size()}\n")
                log_file?.append("Session: ${workflow.sessionId}\n")
                log_file?.append("Duration: ${duration}s\n")
                log_file?.append("Files processed: ${files.size()}\n")
                log_file?.append("\n=== ${pid} finalized: ${timestamp} ===\n\n")
                
                // Write finalization to central pipeline log
                def pipeline_log = new File("${workflow.launchDir}/${params.output_dir}", "pipeline.log")
                pipeline_log.parentFile?.mkdirs()
                if (!pipeline_log.exists()) {
                    try { pipeline_log.text = "" } catch (Exception e) { /* ignore race */ }
                }

                // Add participant log + global pipeline.log to interactive HTML archive
                // HTML lives at the output_dir root, not inside the participant subfolder
                def procedure_html = new File("${workflow.launchDir}/${params.output_dir}", "${params.project_name}_results.html")
                if (procedure_html.exists() && log_file.exists()) {
                    try {
                        def add_log_cmd = [params.python_exe, '-u',
                            "${workflow.launchDir}/${params.toolbox_dir}/utils/interactive_plotter.py",
                            'add-log', procedure_html.absolutePath, pid, log_file.absolutePath, 'Pipeline Log']
                        def proc = add_log_cmd.execute()
                        proc.waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
                        if (proc.exitValue() != 0) {
                            pipeline_log.append("Warning: Failed to add ${pid} log to HTML archive\n")
                        }
                    } catch (Exception e) {
                        pipeline_log.append("Warning: Failed to add ${pid} log to HTML archive: ${e.message}\n")
                    }
                }
                // Also add the global pipeline.log so it appears in the archive
                if (procedure_html.exists() && pipeline_log.exists()) {
                    try {
                        def add_global_cmd = [params.python_exe, '-u',
                            "${workflow.launchDir}/${params.toolbox_dir}/utils/interactive_plotter.py",
                            'add-log', procedure_html.absolutePath, 'global', pipeline_log.absolutePath, 'pipeline.log']
                        def proc2 = add_global_cmd.execute()
                        proc2.waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
                    } catch (Exception e) {
                        pipeline_log.append("Warning: Failed to add global pipeline.log to HTML archive: ${e.message}\n")
                    }
                }

                // Delete the per-participant log — it is now embedded in the HTML archive
                if (log_file.exists()) {
                    log_file.delete()
                }

                pipeline_log.append("\n=== Analysis completed for ${pid}: ${timestamp} ===\n")
                pipeline_log.append("Terminal modules completed: ${files.size()}\n")
                pipeline_log.append("Session: ${workflow.sessionId}\n")
                pipeline_log.append("Duration: ${duration}s\n")
                pipeline_log.append("Files processed: ${files.size()}\n")
                pipeline_log.append("\n=== ${pid} finalized: ${timestamp} ===\n\n")
                
                // Git sync
                git_lock.lock()
                try {
                    def results_full_path = new File("${workflow.launchDir}/${folder}").getAbsoluteFile()
                    def git_root = results_full_path
                    while (git_root != null && !new File(git_root, ".git").exists()) {
                        git_root = git_root.getParentFile()
                    }
                    if (!git_root) return

                    // output_dir is now flat — HTML and plots/ live alongside results files
                    def relative_path     = git_root.toPath().relativize(results_full_path.toPath()).toString().replace('\\', '/')
                    def pipeline_log_path = git_root.toPath().relativize(pipeline_log.toPath()).toString().replace('\\', '/')
                    // HTML and meta.json sit at the output_dir root (parent of participant subfolder)
                    def output_root_full  = results_full_path.parentFile
                    def html_full         = new File(output_root_full, "${params.project_name}_results.html")
                    def meta_full         = new File(output_root_full, "${params.project_name}_meta.json")
                    def html_path         = html_full.exists()  ? git_root.toPath().relativize(html_full.toPath()).toString().replace('\\', '/') : null
                    def meta_path         = meta_full.exists()  ? git_root.toPath().relativize(meta_full.toPath()).toString().replace('\\', '/') : null
                    
                    def runGit = { cmd, timeout = 10 ->
                        try {
                            def env = ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
                            def proc = cmd.execute(env, git_root)
                            def out = new StringBuilder()
                            proc.consumeProcessOutput(out, out)
                            def done = proc.waitFor(timeout, java.util.concurrent.TimeUnit.SECONDS)
                            if (!done) { proc.destroy(); return [exit: -1, out: "timeout"] }
                            return [exit: proc.exitValue(), out: out.toString()]
                        } catch (Exception e) {
                            return [exit: -1, out: e.message]
                        }
                    }
                    
                    def cleanupRebase = {
                        runGit(["git", "rebase", "--abort"], 2)
                        new File(git_root, ".git/rebase-merge").with { if (exists()) deleteDir() }
                        new File(git_root, ".git/rebase-apply").with { if (exists()) deleteDir() }
                    }
                    
                    def logSync = { status, details = "" ->
                        try {
                            pipeline_log.parentFile?.mkdirs()
                            def ts = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                            pipeline_log.append("\n=== Git sync for ${pid}: ${ts} ===\n")
                            pipeline_log.append("Path: ${relative_path}\n")
                            pipeline_log.append("Status: ${status}\n")
                            if (details) pipeline_log.append("${details}\n")
                            pipeline_log.append("=== ${pid} sync complete ===\n\n")
                        } catch (Exception e) { /* non-critical — log write failed, ignore */ }
                    }
                    
                    cleanupRebase()

                    // --- Commit 1: participant subfolder + shared HTML + meta.json (only if changed) ---
                    // NOTE: we add first, then check the staging area — NOT the working tree.
                    // git status --porcelain -uno skips untracked files, which breaks the first
                    // run after switching from PDF plots to parquet sidecars (all new, untracked).
                    def addPaths = [relative_path]
                    if (html_path) addPaths << html_path
                    if (meta_path) addPaths << meta_path

                    // -A stages additions, modifications AND deletions of tracked files.
                    // Plain 'git add' skips deletions, which breaks after per-participant logs
                    // are deleted (they were tracked in older pipeline runs).
                    def add = runGit(["git", "add", "-A"] + addPaths, 30)
                    if (add.exit != 0) {
                        runGit(["git", "reset", "HEAD"], 5)
                        logSync("Git add failed", add.out)
                    } else {
                        // --cached checks the index (what was just staged), not the working tree
                        def status_participant = runGit(["git", "status", "--porcelain", "--cached"] + addPaths, 5)
                        def hasParticipantChanges = status_participant.out?.trim()
                        if (hasParticipantChanges) {
                            def commit = runGit(["git", "commit", "-m", "autosync: ${pid} completed"], 10)
                            if (commit.exit == 0) {
                                def pull = runGit(["git", "pull", "--rebase", "--autostash"], 20)
                                if (pull.exit != 0) {
                                    cleanupRebase()
                                    runGit(["git", "reset", "--hard", "HEAD"], 5)
                                    logSync("Git pull failed", pull.out)
                                } else {
                                    def push = runGit(["git", "push"], 20)
                                    logSync(push.exit == 0 ? "Participant synced" : "Push failed (committed locally)", push.out)
                                }
                            } else {
                                logSync("No participant changes to commit", commit.out)
                                runGit(["git", "pull", "--rebase", "--autostash"], 10)
                                runGit(["git", "push"], 10)
                            }
                        } else {
                            logSync("No participant changes")
                        }
                    }

                    // --- Commit 2: pipeline.log always (captures final sync status above) ---
                    def addLog = runGit(["git", "add", pipeline_log_path], 5)
                    if (addLog.exit == 0) {
                        def commitLog = runGit(["git", "commit", "-m", "pipeline.log: ${pid} complete"], 5)
                        if (commitLog.exit == 0) {
                            runGit(["git", "pull", "--rebase"], 10)
                            runGit(["git", "push"], 10)
                        }
                    }
                    
                } finally {
                    git_lock.unlock()
                }


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
        
        extraArgs = args.collect { "'${escapeArg(it)}'" }.join(' ')
    }
    
    // Extract script name for logging
    def scriptName = script.toString().tokenize('/').last().replace('.py', '')
    
    """
    #!/bin/bash
    
    # Extract participant ID from input filename (pattern like EV_002_*)
    INPUT_FILE=\$(basename "${inputArgs}" | sed "s/'//g")
    PARTICIPANT_ID=\$(echo "\$INPUT_FILE" | grep -oE '^[A-Za-z]+_[0-9]+' | head -1)
    
    # Run processing with logging
    if [ -n "\$PARTICIPANT_ID" ]; then
        LOG_FILE="${workflow.launchDir}/${params.output_dir}/\${PARTICIPANT_ID}/\${PARTICIPANT_ID}_pipeline.log"
        TEMP_OUT=\$(mktemp)
        ${env_exe} -u "${workflow.launchDir}/${script}" ${inputArgs} ${extraArgs} 2>&1 | tee "\$TEMP_OUT"
        EXIT_CODE=\${PIPESTATUS[0]}
        
        # Add timestamp to each line before appending to log
        while IFS= read -r line; do
            echo "\$(date '+%Y-%m-%d %H:%M:%S') \$line" >> "\$LOG_FILE"
        done < "\$TEMP_OUT"
        rm -f "\$TEMP_OUT"
        
        if [ \$EXIT_CODE -ne 0 ]; then
            echo "" >> "\$LOG_FILE"
            echo "\$(date '+%Y-%m-%d %H:%M:%S') [ERROR] ${scriptName} exit code \$EXIT_CODE" >> "\$LOG_FILE"
            exit \$EXIT_CODE
        fi
        
        # Auto-plot any *_vis.parquet files generated by this process (interactive HTML for QC)
        for VIS_FILE in *_vis.parquet; do
            if [ -f "\$VIS_FILE" ]; then
                PREFIX=\$(basename "\$VIS_FILE" _vis.parquet)
                # Create directory first, then resolve absolute path
                mkdir -p "${workflow.launchDir}/${params.output_dir}"
                PROCEDURE_FOLDER="\$(cd "${workflow.launchDir}/${params.output_dir}" && pwd)"
                # PDFs land in the participant subfolder
                PARTICIPANT_DIR="${workflow.launchDir}/${params.output_dir}/\${PARTICIPANT_ID}"
                mkdir -p "\${PARTICIPANT_DIR}"
                PROJECT_NAME="${params.project_name}"
                echo "\$(date '+%Y-%m-%d %H:%M:%S') [autoplot] Creating interactive plot \$VIS_FILE -> \$PROCEDURE_FOLDER/\${PROJECT_NAME}_results.html" >> "\$LOG_FILE"
                # Use interactive plotter for procedure visualization (project-level HTML)
                ${env_exe} -u "${workflow.launchDir}/${params.interactive_plotter_script}" "\$VIS_FILE" "\$PROCEDURE_FOLDER" "\$PREFIX" "\$PROJECT_NAME" 2>&1 | while IFS= read -r line; do
                    echo "\$(date '+%Y-%m-%d %H:%M:%S') \$line" >> "\$LOG_FILE"
                done
                # Remove vis file so it doesn't get passed to next stage
                rm -f "\$VIS_FILE"
            fi
        done
        
        exit 0
    else
        ${env_exe} -u "${workflow.launchDir}/${script}" ${inputArgs} ${extraArgs}
    fi
    """
}
