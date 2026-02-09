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
                def pipeline_log = new File(workflow.launchDir.toString(), "pipeline.log")
                def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                pipeline_log.append("[${timestamp}] [workflow] Git user configured: ${params.git_user_name} <${params.git_user_email}>\n")
            }
        } catch (Exception e) {
            def pipeline_log = new File(workflow.launchDir.toString(), "pipeline.log")
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
        def output_dirs = output_path.exists() ? output_path.list() as Set : [] as Set
        def new_participants = input_path.list().findAll { it.matches(regex_pattern) }.findAll { !(it in output_dirs) }
        
        // Create pipeline log file
        def pipeline_log = new File("${workflow.launchDir}", "pipeline.log")
        if (!pipeline_log.exists()) {
            pipeline_log.text = ""
        }

        def watched_participants = Channel
            .watchPath("${workflow.launchDir}/${input_dir}/*", 'create,modify')
            .map { path -> path.getName() }
            .filter { it.matches(regex_pattern) }
            .unique()

        def all_participants = Channel.fromList(new_participants).concat(watched_participants)
            .filter { pid ->
                def safe_id = pid.replaceAll('\r', '').trim().replaceAll('[^A-Za-z0-9._-]', '_')
                def out_folder = new File("${workflow.launchDir}/${output_dir}/${safe_id}")
                !out_folder.exists()
            }

        participant_context = all_participants.map { pid ->
            def safe_id = pid.replaceAll('\r', '').trim().replaceAll('[^A-Za-z0-9._-]', '_')
            def folder_path = new File("${workflow.launchDir}/${output_dir}/${safe_id}")
            folder_path.mkdirs()
            def log_file = new File(folder_path, "${safe_id}_pipeline.log")
            def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
            
            // Write initial messages (without terminal_modules count)
            log_file.text = "=== ${safe_id} initialized: ${timestamp} ===\n"
            log_file.append("Workflow: ${workflow.projectDir}\n")
            log_file.append("Session: ${workflow.sessionId}\n")
            log_file.append("Launch dir: ${workflow.launchDir}\n")
            log_file.append("Output: ${folder_path}\n")
            log_file.append("\n=== Analysis started for ${safe_id}: ${timestamp} ===\n\n")
            
            // Log to pipeline log (without terminal_modules count)
            def global_pipeline_log = new File("${workflow.launchDir}", "pipeline.log")
            global_pipeline_log.append("=== ${safe_id} initialized: ${timestamp} ===\n")
            global_pipeline_log.append("Workflow: ${workflow.projectDir}\n")
            global_pipeline_log.append("Session: ${workflow.sessionId}\n")
            global_pipeline_log.append("Launch dir: ${workflow.launchDir}\n")
            global_pipeline_log.append("Output: ${folder_path}\n")
            global_pipeline_log.append("\n=== Analysis started for ${safe_id}: ${timestamp} ===\n\n")
            
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
        
        terminal_outputs
            .map { file -> 
                def pid = file.baseName.toString().split('_')[0..1].join('_')
                [pid, file]
            }
            .groupTuple(size: terminal_count)
            .join(participant_context)
            .subscribe { pid, files, folder ->
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
                def pipeline_log = new File("${workflow.launchDir}", "pipeline.log")
                pipeline_log?.append("\n=== Analysis completed for ${pid}: ${timestamp} ===\n")
                pipeline_log?.append("Terminal modules completed: ${files.size()}\n")
                pipeline_log?.append("Session: ${workflow.sessionId}\n")
                pipeline_log?.append("Duration: ${duration}s\n")
                pipeline_log?.append("Files processed: ${files.size()}\n")
                pipeline_log?.append("\n=== ${pid} finalized: ${timestamp} ===\n\n")
                
                // Git sync - last operation
                git_lock.lock()
                try {
                    def results_full_path = new File("${workflow.launchDir}/${folder}").getAbsoluteFile()
                    def git_root = results_full_path
                    while (git_root != null && !new File(git_root, ".git").exists()) {
                        git_root = git_root.getParentFile()
                    }
                    
                    if (!git_root) {
                        sync_log?.append("ERROR: No git repository found\n")
                        return
                    }
                    
                    def relative_path = git_root.toPath().relativize(results_full_path.toPath()).toString().replace('\\', '/')
                    
                    def runGit = { cmd, timeout = 10 ->
                        try {
                            def env = ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
                            def proc = cmd.execute(env, git_root)
                            def out = new StringBuilder()
                            proc.consumeProcessOutput(out, out)
                            def done = proc.waitFor(timeout, java.util.concurrent.TimeUnit.SECONDS)
                            if (!done) {
                                proc.destroy()
                                return [exit: -1, out: "", timeout: true]
                            }
                            return [exit: proc.exitValue(), out: out.toString(), timeout: false]
                        } catch (Exception e) {
                            return [exit: -1, out: e.message, timeout: false]
                        }
                    }
                    
                    def status = runGit(["git", "status", "--porcelain", "-uno", relative_path], 5)
                    
                    if (status.timeout || status.exit != 0 || !status.out?.trim()) {
                        def check_timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        pipeline_log?.append("\n=== Git sync check for ${pid}: ${check_timestamp} ===\n")
                        pipeline_log?.append("Path: ${relative_path}\n")
                        pipeline_log?.append("Status: No changes to sync\n")
                        pipeline_log?.append("=== ${pid} thread closed: ${check_timestamp} ===\n\n")
                        return
                    }
                    
                    def syncFailed = false
                    def syncError = ""
                    
                    // Add participant folder (includes all files in it)
                    def add = runGit(["git", "add", relative_path], 10)
                    if (add.timeout || add.exit != 0) {
                        def error_timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        pipeline_log?.append("\n=== Git sync check for ${pid}: ${error_timestamp} ===\n")
                        pipeline_log?.append("Path: ${relative_path}\n")
                        pipeline_log?.append("Error: git add failed - ${add.out?.trim() ?: 'timeout'}\n")
                        pipeline_log?.append("=== ${pid} thread closed: ${error_timestamp} ===\n\n")
                        return
                    }
                    
                    // Add pipeline.log
                    def pipeline_log_path = git_root.toPath().relativize(pipeline_log.toPath()).toString().replace('\\', '/')
                    def log_add = runGit(["git", "add", pipeline_log_path], 10)
                    if (log_add.timeout || log_add.exit != 0) {
                        def error_timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        pipeline_log?.append("\n=== Git sync check for ${pid}: ${error_timestamp} ===\n")
                        pipeline_log?.append("Path: ${relative_path}\n")
                        pipeline_log?.append("Error: git add pipeline.log failed - ${log_add.out?.trim() ?: 'timeout'}\n")
                        pipeline_log?.append("=== ${pid} thread closed: ${error_timestamp} ===\n\n")
                        return
                    }
                    
                    def commit = runGit(["git", "commit", "-m", "autosync: ${pid} completed"], 10)
                    if (commit.timeout) {
                        def error_timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        pipeline_log?.append("\n=== Git sync check for ${pid}: ${error_timestamp} ===\n")
                        pipeline_log?.append("Path: ${relative_path}\n")
                        pipeline_log?.append("Error: git commit timed out\n")
                        pipeline_log?.append("=== ${pid} thread closed: ${error_timestamp} ===\n\n")
                        return
                    }
                    if (commit.exit != 0) {
                        def no_commit_timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        pipeline_log?.append("\n=== Git sync check for ${pid}: ${no_commit_timestamp} ===\n")
                        pipeline_log?.append("Path: ${relative_path}\n")
                        pipeline_log?.append("Status: No new commits\n")
                        pipeline_log?.append("Git commit output: ${commit.out?.trim()}\n")
                        pipeline_log?.append("=== ${pid} thread closed: ${no_commit_timestamp} ===\n\n")
                        
                        // Commit pipeline.log to track thread closure
                        runGit(["git", "add", "pipeline.log"], 5)
                        runGit(["git", "commit", "-m", "pipeline.log: ${pid} thread closed (no new results)"], 5)
                        runGit(["git", "pull", "--rebase", "--autostash"], 10)
                        runGit(["git", "push"], 10)
                        return
                    }
                    
                    def pull = runGit(["git", "pull", "--rebase", "--autostash"], 20)
                    if (pull.timeout) {
                        def error_timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        runGit(["git", "rebase", "--abort"], 5)
                        pipeline_log?.append("\n=== Git sync check for ${pid}: ${error_timestamp} ===\n")
                        pipeline_log?.append("Path: ${relative_path}\n")
                        pipeline_log?.append("Error: git pull timed out\n")
                        pipeline_log?.append("=== ${pid} thread closed: ${error_timestamp} ===\n\n")
                        
                        // Commit pipeline.log to track thread closure
                        runGit(["git", "add", "pipeline.log"], 5)
                        runGit(["git", "commit", "-m", "pipeline.log: ${pid} thread closed (pull timeout)"], 5)
                        runGit(["git", "push"], 10)
                        return
                    }
                    if (pull.exit != 0) {
                        def error_timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        runGit(["git", "rebase", "--abort"], 5)
                        
                        // Check if it's an authentication error
                        def isAuthError = pull.out?.contains("Authentication failed") || pull.out?.contains("Invalid username")
                        if (isAuthError) {
                            pipeline_log?.append("\n=== Git sync check for ${pid}: ${error_timestamp} ===\n")
                            pipeline_log?.append("Path: ${relative_path}\n")
                            pipeline_log?.append("Status: Committed locally (push skipped - authentication required)\n")
                            pipeline_log?.append("Note: Results are saved locally. Configure SSH keys or access token for remote sync.\n")
                            pipeline_log?.append("=== ${pid} thread closed: ${error_timestamp} ===\n\n")
                            
                            // Commit pipeline.log to track thread closure (local only)
                            runGit(["git", "add", "pipeline.log"], 5)
                            runGit(["git", "commit", "-m", "pipeline.log: ${pid} thread closed (auth required)"], 5)
                            return
                        }
                        
                        pipeline_log?.append("\n=== Git sync check for ${pid}: ${error_timestamp} ===\n")
                        pipeline_log?.append("Path: ${relative_path}\n")
                        pipeline_log?.append("Error: git pull failed - ${pull.out?.trim()}\n")
                        pipeline_log?.append("=== ${pid} thread closed: ${error_timestamp} ===\n\n")
                        
                        // Commit pipeline.log to track thread closure
                        runGit(["git", "add", "pipeline.log"], 5)
                        runGit(["git", "commit", "-m", "pipeline.log: ${pid} thread closed (pull failed)"], 5)
                        runGit(["git", "push"], 10)
                        return
                    }
                    
                    def push = runGit(["git", "push"], 20)
                    if (push.timeout) {
                        def error_timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        pipeline_log?.append("\n=== Git sync check for ${pid}: ${error_timestamp} ===\n")
                        pipeline_log?.append("Path: ${relative_path}\n")
                        pipeline_log?.append("Status: Committed locally (push timed out)\n")
                        pipeline_log?.append("=== ${pid} thread closed: ${error_timestamp} ===\n\n")
                        
                        // Commit pipeline.log to track thread closure (local only)
                        runGit(["git", "add", "pipeline.log"], 5)
                        runGit(["git", "commit", "-m", "pipeline.log: ${pid} thread closed (push timeout)"], 5)
                        return
                    }
                    if (push.exit != 0) {
                        def error_timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        
                        // Check if it's an authentication error
                        def isAuthError = push.out?.contains("Authentication failed") || push.out?.contains("Invalid username")
                        if (isAuthError) {
                            pipeline_log?.append("\n=== Git sync check for ${pid}: ${error_timestamp} ===\n")
                            pipeline_log?.append("Path: ${relative_path}\n")
                            pipeline_log?.append("Status: Committed locally (push skipped - authentication required)\n")
                            pipeline_log?.append("Note: Results are saved locally. Configure SSH keys or access token for remote sync.\n")
                            pipeline_log?.append("=== ${pid} thread closed: ${error_timestamp} ===\n\n")
                            
                            // Commit pipeline.log to track thread closure (local only)
                            runGit(["git", "add", "pipeline.log"], 5)
                            runGit(["git", "commit", "-m", "pipeline.log: ${pid} thread closed (auth required)"], 5)
                            return
                        }
                        
                        pipeline_log?.append("\n=== Git sync check for ${pid}: ${error_timestamp} ===\n")
                        pipeline_log?.append("Path: ${relative_path}\n")
                        pipeline_log?.append("Error: git push failed - ${push.out?.trim()}\n")
                        pipeline_log?.append("=== ${pid} thread closed: ${error_timestamp} ===\n\n")
                        
                        // Commit pipeline.log to track thread closure (local only)
                        runGit(["git", "add", "pipeline.log"], 5)
                        runGit(["git", "commit", "-m", "pipeline.log: ${pid} thread closed (push failed)"], 5)
                        return
                    }
                    
                    // Log successful sync
                    try {
                        def sync_timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        
                        pipeline_log.append("\n=== Git sync check for ${pid}: ${sync_timestamp} ===\n")
                        pipeline_log.append("Path: ${relative_path}\n")
                        pipeline_log.append("Status: synced (${sync_timestamp})\n")
                        pipeline_log.append("=== ${pid} thread closed: ${sync_timestamp} ===\n\n")
                        
                        // Commit pipeline.log to track thread closure
                        runGit(["git", "add", "pipeline.log"], 5)
                        runGit(["git", "commit", "-m", "pipeline.log: ${pid} thread closed"], 5)
                        runGit(["git", "pull", "--rebase", "--autostash"], 10)
                        runGit(["git", "push"], 10)
                    } catch (Exception e) {
                        // Ignore log sync errors - main sync already succeeded
                    }
                } catch (Exception e) {
                    def err_timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                    pipeline_log?.append("\n=== Git sync check for ${pid}: ${err_timestamp} ===\n")
                    pipeline_log?.append("Path: ${relative_path}\n")
                    pipeline_log?.append("Error: ${e.message}\n")
                    pipeline_log?.append("=== ${pid} thread closed: ${err_timestamp} ===\n\n")
                    
                    // Commit pipeline.log to track thread closure
                    try {
                        runGit(["git", "add", "pipeline.log"], 5)
                        runGit(["git", "commit", "-m", "pipeline.log: ${pid} thread closed (exception)"], 5)
                        runGit(["git", "push"], 10)
                    } catch (Exception logErr) {
                        // Ignore - already in error state
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
        path "*.{fif,parquet}"

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
        fi
        exit \$EXIT_CODE
    else
        ${env_exe} -u "${workflow.launchDir}/${script}" ${inputArgs} ${extraArgs}
    fi
    """
}
