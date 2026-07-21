workflow data_discovery {
    take:
        input_dir
        output_dir
        participant_pattern

    main:
        // BEHOBEN: .toString() zwingt UnixPath in einen sauberen String für das Java-File-Objekt!
        def utilsGroovyFile = new File(moduleDir.toString(), "utils/discoveryUtils.groovy")
        def currentLoader   = Thread.currentThread().contextClassLoader
        def utilsClass      = new GroovyClassLoader(currentLoader).parseClass(utilsGroovyFile)


        def regex_pattern = participant_pattern.replaceAll(/\*/, '.*').replaceAll(/\?/, '.')
        def input_path = new File("${workflow.launchDir}/${input_dir}")
        def output_path = new File("${workflow.launchDir}/${output_dir}")
        
        def l1_path = new File(output_path, "${params.project_name}_l1")
        def output_dirs = l1_path.exists() ? l1_path.list() as Set : [] as Set
        
        def reinject_pids = l1_path.exists()
            ? l1_path.listFiles().findAll { dir_item -> dir_item.isDirectory() && new File(dir_item, ".reinject").exists() }.collect { dir_obj -> dir_obj.name } as Set
            : [] as Set
            
        def new_participants = input_path.list().findAll { p_id -> p_id.matches(regex_pattern) }.findAll { p_id -> !(p_id in output_dirs) || p_id in reinject_pids }
        
        def bin_dir_infra = new File("${workflow.launchDir}/${params.output_dir}", ".bin")
        bin_dir_infra.mkdirs()
        def pipeline_log = new File(bin_dir_infra, "${params.project_name}.log")
        if (!pipeline_log.exists()) {
            pipeline_log.text = ""
        }

        if (params.l2_analyses) {
            def l2_dir  = new File("${workflow.launchDir}/${params.output_dir}", "${params.project_name}_l2")
            def l2_plots_dir = new File(l2_dir, "plots")
            def l2_tables_dir = new File(l2_dir, "tables")
            def l2_results_dir = new File(l2_dir, "results")
            l2_dir.mkdirs()
            l2_plots_dir.mkdirs()
            l2_tables_dir.mkdirs()
            l2_results_dir.mkdirs()
        }

        def l1_dir_scaffold = new File("${workflow.launchDir}/${params.output_dir}", "${params.project_name}_l1")
        l1_dir_scaffold.mkdirs()

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

        if (output_path.exists()) {
            output_path.eachDirRecurse { sub_dir ->
                def marker = new File(sub_dir, ".finalized")
                if (marker.exists()) marker.delete()
            }
        }

        // Bootstrap push
        try {
            def git_root = utilsClass.findGitRoot(new File(workflow.launchDir.toString()))
            if (git_root) {
                def inheritedEnv = System.getenv().collect { env_k, env_v -> "${env_k}=${env_v}" } + ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
                
                new File(git_root, ".git/index.lock").with { lock_f -> if (lock_f.exists()) lock_f.delete() }
                new File(git_root, ".git/rebase-merge").with { rm_f -> if (rm_f.exists()) rm_f.deleteDir() }
                new File(git_root, ".git/rebase-apply").with { ra_f -> if (ra_f.exists()) ra_f.deleteDir() }

                def binIgnore = new File(bin_dir_infra, ".gitignore")
                def ignorePatterns = ["pipeline_trace.txt", "*.log", "*_results.html"]
                ignorePatterns.each { pattern ->
                    if (!binIgnore.exists() || !binIgnore.text.contains(pattern)) {
                        binIgnore.append("${pattern}\n")
                    }
                }
                
                def binRel = git_root.toPath().relativize(bin_dir_infra.toPath().toAbsolutePath()).toString().replace('\\', '/')
                utilsClass.executeBootstrapGit(["git", "rm", "-r", "--cached", "--ignore-unmatch", "--", binRel], inheritedEnv, git_root)
                
                def nfLog = new File(workflow.launchDir.toString(), ".nextflow.log")
                if (nfLog.exists()) {
                    def nfLogRel = git_root.toPath().relativize(nfLog.toPath().toAbsolutePath()).toString().replace('\\', '/')
                    utilsClass.executeBootstrapGit(["git", "rm", "--cached", "--ignore-unmatch", "--", nfLogRel], inheritedEnv, git_root)
                }

                def outputRel = git_root.toPath().relativize(
                    new File("${workflow.launchDir}/${output_dir}").getAbsoluteFile().toPath()
                ).toString().replace('\\', '/')
                def addAll = utilsClass.executeBootstrapGit(["git", "add", "-A", outputRel], inheritedEnv, git_root)
                if (addAll.exit == 0) {
                    def st = utilsClass.executeBootstrapGit(["git", "status", "--porcelain", "--cached"], inheritedEnv, git_root)
                    if (st.out?.trim()) {
                        def ts = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
                        utilsClass.executeBootstrapGit(["git", "commit", "-m", "autosync: bootstrap push before analysis (${ts})"], inheritedEnv, git_root)
                        
                        def pull = utilsClass.executeBootstrapGit(["git", "pull", "--rebase"], inheritedEnv, git_root)
                        if (pull.exit != 0) {
                            utilsClass.executeBootstrapGit(["git", "rebase", "--abort"], inheritedEnv, git_root)
                            new File(git_root, ".git/index.lock").with { lock_f2 -> if (lock_f2.exists()) lock_f2.delete() }
                        }
                        utilsClass.executeBootstrapGit(["git", "push"], inheritedEnv, git_root)
                    }
                }
                pipeline_log.append("[${new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())}] [workflow] Bootstrap push completed\n")
            }
        } catch (Exception e) {
            pipeline_log.append("[${new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())}] [workflow] Bootstrap push failed: ${e.message}\n")
        }

        def all_participants
        if (params.watch) {
            def watched_participants = channel
                .watchPath("${workflow.launchDir}/${input_dir}/*", 'create,modify')
                .map { path -> path.getName() }
                .filter { raw_pid -> raw_pid.matches(regex_pattern) }
                .unique()
            
            all_participants = channel.fromList(new_participants).concat(watched_participants)
                .filter { pid ->
                    def safe_id = utilsClass.cleanParticipantId(pid)
                    def pid_dir = new File("${workflow.launchDir}/${output_dir}/${params.project_name}_l1/${safe_id}")
                    return !pid_dir.exists() || new File(pid_dir, ".reinject").exists()
                }
        } else {
            all_participants = channel.fromList(new_participants)
        }

    emit:
        // BEHOBEN: Wir hängen die Transformation DIREKT und nackt an den emittierten Kanal.
        // Keine ungenutzte Variable, kein verbotener Zuweisungs-Name!
        all_participants.map { pid ->
            def safe_id = utilsClass.cleanParticipantId(pid)
            def participant_dir = new File(l1_dir_scaffold, safe_id)
            participant_dir.mkdirs()
            
            def timestamp = new java.text.SimpleDateFormat('yyyy-MM-dd HH:mm:ss').format(new Date())
            def global_pipeline_log = new File(new File("${workflow.launchDir}/${params.output_dir}", ".bin"), "${params.project_name}.log")
            global_pipeline_log.append("=== ${safe_id} initialized: ${timestamp} ===\nOutput: ${participant_dir}\n\n")
            
            def folder = "${output_dir}/${params.project_name}_l1/${safe_id}"
            return [pid, folder]
        }
}

