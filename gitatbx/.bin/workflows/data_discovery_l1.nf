workflow data_discovery_l1 {
    take:
        input_dir
        output_dir
        participant_pattern

    main:
        if (!input_dir) error "[data_discovery_l1] CRITICAL: 'input_dir' parameter is required."
        if (!output_dir) error "[data_discovery_l1] CRITICAL: 'output_dir' parameter is required."
        if (!participant_pattern) error "[data_discovery_l1] CRITICAL: 'participant_pattern' parameter is required."
        if (!params.project_name) error "[data_discovery_l1] CRITICAL: 'params.project_name' must be specified in configuration."

        def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
        
        def pathUtilsFile = moduleDir.resolve("../lib/PathUtils.groovy").toFile()
        def baseFsFile    = moduleDir.resolve("../lib/base/BaseFileSystemUtils.groovy").toFile()
        def baseGitFile   = moduleDir.resolve("../lib/base/BaseGitUtils.groovy").toFile()

        if (!pathUtilsFile.exists()) error "[data_discovery_l1] Missing library: ${pathUtilsFile.absolutePath}"
        if (!baseFsFile.exists())    error "[data_discovery_l1] Missing library: ${baseFsFile.absolutePath}"
        if (!baseGitFile.exists())   error "[data_discovery_l1] Missing library: ${baseGitFile.absolutePath}"

        gcl.addClasspath(moduleDir.resolve('../').toFile().absolutePath)

        def PathUtils           = gcl.parseClass(pathUtilsFile)
        def BaseFileSystemUtils = gcl.parseClass(baseFsFile)
        def BaseGitUtils        = gcl.parseClass(baseGitFile)

        def regex_pattern   = PathUtils.globToRegex(participant_pattern)
        def input_path      = new File("${workflow.launchDir}/${input_dir}")
        def output_path     = new File("${workflow.launchDir}/${output_dir}")

        if (!input_path.exists()) {
            error "[data_discovery_l1] CRITICAL: Input directory does not exist: ${input_path.absolutePath}"
        }

        def l1_path       = new File(output_path, "${params.project_name}_l1")
        def output_dirs   = l1_path.exists() ? l1_path.list() as Set : [] as Set
        def reinject_pids = l1_path.exists()
            ? l1_path.listFiles().findAll { java.io.File dir -> dir.isDirectory() && new File(dir, ".reinject").exists() }.collect { java.io.File dir -> dir.name } as Set
            : [] as Set
            
        def input_files      = input_path.exists() ? input_path.list() : []
        def new_participants = input_files
            .findAll { String pid -> pid.matches(regex_pattern) }
            .findAll { String pid -> !(pid in output_dirs) || pid in reinject_pids }
        
        def bin_dir_infra = new File("${workflow.launchDir}/${params.output_dir}", ".bin")
        bin_dir_infra.mkdirs()
        def pipeline_log  = new File(bin_dir_infra, "${params.project_name}.log")

        BaseFileSystemUtils.appendLog(pipeline_log, "[data_discovery_l1] Procedure Initialized. Scanning ${input_path}...")

        if (params.l2_analyses) {
            new File("${workflow.launchDir}/${params.output_dir}", "${params.project_name}_l2/.bin").mkdirs()
        }

        def l1_dir_scaffold = new File("${workflow.launchDir}/${params.output_dir}", "${params.project_name}_l1")
        l1_dir_scaffold.mkdirs()

        // Purge markers
        BaseFileSystemUtils.removeMarkers(output_path, ".finalized")

        // Git Bootstrap (Fail-First)
        def git_root = BaseGitUtils.findGitRoot(new File(workflow.launchDir.toString()))
        if (git_root) {
            def inheritedEnv = System.getenv().collect { String envKey, String envVal -> "${envKey}=${envVal}" } + ["GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=echo", "SSH_ASKPASS=echo"]
            def binRel    = git_root.toPath().relativize(bin_dir_infra.toPath().toAbsolutePath()).toString().replace('\\', '/')
            def outputRel = git_root.toPath().relativize(output_path.getAbsoluteFile().toPath()).toString().replace('\\', '/')
            
            BaseGitUtils.syncBootstrap(git_root, outputRel, binRel, inheritedEnv)
        }

        def all_participants
        if (params.watch) {
            def watched_participants = channel
                .watchPath("${workflow.launchDir}/${input_dir}/*", 'create,modify')
                .map { java.nio.file.Path path -> path.getName() }
                .filter { String raw_pid -> raw_pid.matches(regex_pattern) }
            
            all_participants = channel.fromList(new_participants)
                .concat(watched_participants)
                .unique()
                .filter { String pid ->
                    def safe_id = PathUtils.cleanId(pid)
                    def pid_dir = new File(l1_dir_scaffold, safe_id)
                    return !pid_dir.exists() || new File(pid_dir, ".reinject").exists()
                }
        } else {
            all_participants = channel.fromList(new_participants)
        }

        BaseFileSystemUtils.appendLog(pipeline_log, "[data_discovery_l1] Discovery pass completed.")

    emit:
        all_participants.map { String pid ->
            def safe_id = PathUtils.cleanId(pid)
            def participant_dir = new File(l1_dir_scaffold, safe_id)
            participant_dir.mkdirs()
            new File(participant_dir, ".bin").mkdirs()
            
            def raw_input_folder = new File(input_path, pid).absolutePath
            def l1_folder = "${output_dir}/${params.project_name}_l1/${safe_id}"
            
            return [safe_id, raw_input_folder, l1_folder]
        }
}