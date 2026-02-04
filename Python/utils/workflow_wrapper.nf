import java.util.concurrent.locks.ReentrantLock

// Shared git lock for sequential git operations across participants
@groovy.transform.Field
static java.util.concurrent.locks.ReentrantLock git_lock = new java.util.concurrent.locks.ReentrantLock()

// Enhanced participant_discovery: discovers participants, creates output folders
workflow participant_discovery {
    take:
        input_dir
        output_dir
        participant_pattern

    main:
        // Convert glob pattern to regex
        def regex_pattern = participant_pattern.replaceAll(/\*/, '.*').replaceAll(/\?/, '.')
        def input_path = new File("${workflow.launchDir}/${input_dir}")
        def output_path = new File("${workflow.launchDir}/${output_dir}")
        def output_dirs = output_path.exists() ? output_path.list() as Set : [] as Set
        def new_participants = input_path.list().findAll { it.matches(regex_pattern) }.findAll { !(it in output_dirs) }

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
            
            // Write initial messages
            log_file.text = "=== Analysis started for ${safe_id}: ${timestamp} ===\n"
            log_file.append("Workflow: ${workflow.projectDir}\n")
            log_file.append("Session: ${workflow.sessionId}\n")
            log_file.append("Launch dir: ${workflow.launchDir}\n")
            log_file.append("Output: ${folder_path}\n")
            log_file.append("Expected terminal modules: 17\n")
            
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
        terminal_plots.each { ch -> terminal_outputs = terminal_outputs.mix(ch) }
        
        terminal_outputs
            .map { file -> 
                def pid = file.baseName.toString().split('_')[0..1].join('_')
                [pid, file]
            }
            .groupTuple(size: 17)
            .join(participant_context)
            .subscribe { pid, files, folder ->
                Thread.sleep(2000)
                
                def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                def log_file = new File("${workflow.launchDir}/${folder}/${pid}_pipeline.log")
                def start_time = log_file.exists() ? new Date(log_file.lastModified()) : new Date()
                def duration = (new Date().time - start_time.time) / 1000
                
                // Write finalization BEFORE git sync
                log_file?.append("\n=== Analysis completed for ${pid}: ${timestamp} ===\n")
                log_file?.append("Terminal modules completed: ${files.size()}/17\n")
                log_file?.append("\n=== Finalization complete for ${pid}: ${timestamp} ===\n")
                log_file?.append("Session: ${workflow.sessionId}\n")
                log_file?.append("Duration: ${duration}s\n")
                log_file?.append("Files processed: ${files.size()}\n")
                
                def logMsg = { msg -> log_file?.append("${timestamp} [wrapper] ${msg}\n") }
                
                // Git sync - last operation
                git_lock.lock()
                try {
                    def results_full_path = new File("${workflow.launchDir}/${folder}").getAbsoluteFile()
                    def git_root = results_full_path
                    while (git_root != null && !new File(git_root, ".git").exists()) {
                        git_root = git_root.getParentFile()
                    }
                    
                    if (!git_root) {
                        logMsg("ERROR: No git repository found")
                        return
                    }
                    
                    def relative_path = git_root.toPath().relativize(results_full_path.toPath()).toString().replace('\\', '/')
                    logMsg("Git sync initialized for ${relative_path}")
                    
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
                    if (status.timeout) {
                        logMsg("ERROR: Git status timed out")
                        return
                    }
                    if (status.exit != 0 || !status.out?.trim()) return
                    
                    def add = runGit(["git", "add", relative_path], 5)
                    if (add.timeout || add.exit != 0) {
                        logMsg("ERROR: git add failed - ${add.out?.trim() ?: 'timeout'}")
                        return
                    }
                    
                    def commit = runGit(["git", "commit", "-m", "autosync: ${pid} completed"], 10)
                    if (commit.timeout) {
                        logMsg("ERROR: git commit timed out")
                        return
                    }
                    if (commit.exit != 0) return
                    
                    def pull = runGit(["git", "pull", "--rebase", "--autostash"], 20)
                    if (pull.timeout) {
                        logMsg("ERROR: git pull timed out")
                        runGit(["git", "rebase", "--abort"], 5)
                        return
                    }
                    if (pull.exit != 0) {
                        logMsg("ERROR: git pull failed - ${pull.out?.trim()}")
                        runGit(["git", "rebase", "--abort"], 5)
                        return
                    }
                    
                    def push = runGit(["git", "push"], 20)
                    if (push.timeout) {
                        logMsg("ERROR: git push timed out")
                        return
                    }
                    if (push.exit != 0) {
                        logMsg("ERROR: git push failed - ${push.out?.trim()}")
                        return
                    }
                } catch (Exception e) {
                    logMsg("ERROR: ${e.message}")
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
