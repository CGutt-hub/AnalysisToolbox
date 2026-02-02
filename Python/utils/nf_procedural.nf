// Enhanced workflow_wrapper: discovers participants, creates output folders, tracks terminality, and finalizes (logging + git sync)
// Automatically detects terminal modules as those not consumed by other processes (final branches)
workflow workflow_wrapper {
    take:
        input_dir
        output_dir
        participant_pattern
        terminal_process_names

    main:
        // Convert glob pattern to regex
        def regex_pattern = participant_pattern.replaceAll(/\*/, '.*').replaceAll(/\?/, '.')
        def input_path = new File("${workflow.launchDir}/${input_dir}")
        def output_path = new File("${workflow.launchDir}/${output_dir}")
        def output_dirs = output_path.exists() ? output_path.list() as Set : [] as Set
        def new_participants = input_path.list().findAll { it.matches(regex_pattern) }.findAll { !(it in output_dirs) }

        watched_participants = Channel
            .watchPath("${workflow.launchDir}/${input_dir}/*", 'create,modify')
            .map { path -> path.getName() }
            .filter { it.matches(regex_pattern) }
            .unique()

        all_participants = Channel.fromList(new_participants).concat(watched_participants)
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
            log_file.text = "=== Pipeline started: ${new Date().format('yyyy-MM-dd HH:mm:ss')} for ${safe_id} ===\n"
            def folder = "${output_dir}/${safe_id}"
            [pid, folder]
        }

        // Terminal modules: passed as parameter
        def effective_terminal_modules = terminal_process_names

        // Terminality tracking via trace file
        def output_base = "${workflow.launchDir}/${output_dir}"
        
        // Read existing trace file first for already-completed participants
        def trace_file = new File("${workflow.launchDir}/pipeline_trace.txt")
        def initial_completions = Channel.empty()
        if (trace_file.exists() && trace_file.size() > 0) {
            initial_completions = Channel.fromPath("${workflow.launchDir}/pipeline_trace.txt")
                .splitCsv(sep: '\t', header: true)
                .filter { row -> row.name in effective_terminal_modules && row.status in ['COMPLETED', 'FAILED', 'CACHED'] }
                .map { row -> 
                    def tag = row.tag ?: ''
                    def pidMatch = tag =~ /^([A-Za-z]+_\d+)/
                    def pid = pidMatch ? pidMatch[0][1] : ''
                    [pid, row.name, row.status]
                }
                .filter { pid, name, status -> pid != '' }
        }
        
        // Watch for new completions
        def new_completions = Channel.watchPath("${workflow.launchDir}/pipeline_trace.txt", 'modify')
            .splitCsv(sep: '\t', header: true)
            .filter { row -> row.name in effective_terminal_modules && row.status in ['COMPLETED', 'FAILED', 'CACHED'] }
            .map { row -> 
                // Extract participant ID from tag (e.g., "EV_002_sam_concat.parquet" -> "EV_002")
                def tag = row.tag ?: ''
                def pidMatch = tag =~ /^([A-Za-z]+_\d+)/
                def pid = pidMatch ? pidMatch[0][1] : ''
                [pid, row.name, row.status]
            }
            .filter { pid, name, status -> pid != '' } // only process if PID found
        
        // Combine initial and new completions
        trace_updates = initial_completions.concat(new_completions)
            .unique { "${it[0]}_${it[1]}" } // unique by pid and module

        participant_statuses = trace_updates
            .groupTuple()
            .map { pid, modules, statuses ->
                // Debug: log what we have
                def log_file = new File("${workflow.launchDir}/${output_dir}/${pid}/${pid}_pipeline.log")
                if (log_file.exists()) {
                    def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                    log_file.append("[${timestamp}] [finalize] Checking completion: ${modules.size()}/${effective_terminal_modules.size()} terminal modules\n")
                    log_file.append("[${timestamp}] [finalize] Completed: ${modules}\n")
                    def missing = effective_terminal_modules - modules
                    if (missing) {
                        log_file.append("[${timestamp}] [finalize] Still waiting for: ${missing}\n")
                    }
                }
                [pid, modules, statuses]
            }
            .filter { pid, modules, statuses ->
                def all_present = modules.containsAll(effective_terminal_modules)
                if (all_present) {
                    def log_file = new File("${workflow.launchDir}/${output_dir}/${pid}/${pid}_pipeline.log")
                    if (log_file.exists()) {
                        def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        log_file.append("[${timestamp}] [finalize] All terminal modules complete - triggering finalization\n")
                    }
                }
                all_present
            }
            .map { pid, modules, statuses ->
                def module_statuses = [modules, statuses].transpose()
                [pid, module_statuses, output_dir]
            }

        // Finalization (logging + git sync)
        participant_finalized = participant_statuses.map { pid, module_statuses, results_path ->
            def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
            def log_file = new File("${workflow.launchDir}/${results_path}/${pid}/${pid}_pipeline.log")
            println("[workflow] Starting finalization for ${pid}")
            if (log_file.exists()) {
                log_file.append("\n=== Pipeline completed: ${timestamp} ===\n")
                log_file.append("=== Terminal modules status: ===\n")
                module_statuses.each { mod, status ->
                    log_file.append("    ${mod}: ${status}\n")
                }
                log_file.append("=== Starting finalization: ${timestamp} for ${pid} ===\n\n")
            } else {
                println("[workflow] Warning: Log file not found: ${log_file.absolutePath}")
            }
            git_lock.lock()
            try {
                // Resolve full path to results
                def results_full_path = new File("${workflow.launchDir}/${results_path}/${pid}").getAbsoluteFile()
                log_file?.append("[${timestamp}] [finalize] Results path: ${results_full_path}\n")
                
                // Find git root
                def git_root = results_full_path
                while (git_root != null && !new File(git_root, ".git").exists()) {
                    git_root = git_root.getParentFile()
                }
                if (git_root == null) {
                    log_file?.append("[${timestamp}] [finalize] Git error: No git repository found (searched from ${results_full_path})\n")
                    return pid
                }
                def git_root_path = git_root.getAbsolutePath()
                log_file?.append("[${timestamp}] [finalize] Git repository found: ${git_root_path}\n")
                def runGit = { List<String> cmd ->
                    try {
                        def proc = cmd.execute(null, git_root)
                        def stdout = new StringBuilder()
                        def stderr = new StringBuilder()
                        proc.consumeProcessOutput(stdout, stderr)
                        def exitOk = proc.waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
                        if (!exitOk) {
                            proc.destroy()
                            return [exit: -1, out: "", err: "timeout"]
                        }
                        return [exit: proc.exitValue(), out: stdout.toString(), err: stderr.toString()]
                    } catch (Exception e) {
                        return [exit: -1, out: "", err: e.message]
                    }
                }
                def status = runGit(["git", "status", "--porcelain"])
                log_file?.append("[${timestamp}] [finalize] Git status check: exit=${status.exit}, changes=${status.out.trim() ? 'yes' : 'no'}\n")
                if (status.exit != 0) {
                    log_file?.append("[${timestamp}] [finalize] Git status error: ${status.err}\n")
                } else if (status.out.trim()) {
                    log_file?.append("[${timestamp}] [finalize] Changes detected, syncing...\n")
                    def pull = runGit(["git", "pull", "--rebase", "--autostash"])
                    log_file?.append("[${timestamp}] [finalize] Pull: ${pull.exit == 0 ? 'ok' : 'error'}${pull.err ? ' - ' + pull.err : ''}\n")
                    def add = runGit(["git", "add", "."])
                    log_file?.append("[${timestamp}] [finalize] Add: ${add.exit == 0 ? 'ok' : 'error'}${add.err ? ' - ' + add.err : ''}\n")
                    def failed_modules = module_statuses.findAll { it[1] != 'COMPLETED' }.collect { it[0] }
                    def overall_status = failed_modules ? "failed (${failed_modules.join(', ')})" : 'succeeded'
                    def msg = "autosync results: ${pid} ${overall_status}"
                    def commit = runGit(["git", "commit", "-m", msg])
                    log_file?.append("[${timestamp}] [finalize] Commit: ${commit.exit == 0 ? 'ok' : 'skip'}${commit.err ? ' - ' + commit.err : ''}\n")
                    if (commit.exit == 0) {
                        def push = runGit(["git", "push"])
                        log_file?.append("[${timestamp}] [finalize] Push (attempt 1): ${push.exit == 0 ? 'ok' : 'retry'}${push.err ? ' - ' + push.err : ''}\n")
                        if (push.exit != 0) {
                            runGit(["git", "pull", "--rebase", "--autostash"])
                            push = runGit(["git", "push"])
                            log_file?.append("[${timestamp}] [finalize] Push (attempt 2): ${push.exit == 0 ? 'ok' : 'failed'}${push.err ? ' - ' + push.err : ''}\n")
                        }
                    }
                } else {
                    log_file?.append("[${timestamp}] [finalize] No changes to sync\n")
                }
                log_file?.append("[${timestamp}] [finalize] Git sync completed successfully\n")
            } catch (Exception e) {
                log_file?.append("[${timestamp}] [finalize] EXCEPTION during git sync: ${e.class.name}: ${e.message}\n")
                e.printStackTrace()
                def sw = new StringWriter()
                e.printStackTrace(new PrintWriter(sw))
                log_file?.append("[${timestamp}] [finalize] Stack trace:\n${sw.toString()}\n")
            } finally {
                git_lock.unlock()
                log_file?.append("\n=== Finalization complete: ${new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())} for ${pid} ===\n")
                println("[workflow] Finalization complete for ${pid}")
            }
            return pid
        }

    emit:
        participant_context
        participant_finalized
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
