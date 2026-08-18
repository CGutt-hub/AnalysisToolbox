nextflow.enable.dsl=2

process NATIVE_SPLIT {
    executor 'local'

    publishDir (
        path: {
            def isL2 = (level_tag ? level_tag.toString().trim().toUpperCase() : "L1") == "L2"
            def l2FolderVal = params.l2_folder ? params.l2_folder.toString().trim() : "${params.project_name}_l2"
            return isL2 ? 
                "${params.output_dir}/${l2FolderVal}/.bin" : 
                "${params.output_dir}/${params.project_name}_l1/${target_id}/.bin"
        },
        mode: 'copy',
        pattern: "*.{fif,parquet}"
    )

    input:
        path context_path
        val  full_pattern
        val  level_tag       // Explicitly 'L1' or 'L2'
        val  target_id_val   // Optional explicit participant/cohort ID

    output:
        path "*.{fif,parquet}", emit: isolated_file
        path "*.log"          , emit: log_file

    exec:
        def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)

        def jarDirs = [
            moduleDir.resolve('../lib/jars').toFile(),
            moduleDir.resolve('../../lib/jars').toFile(),
            moduleDir.resolve('../lib').toFile(),
            moduleDir.resolve('../../lib').toFile()
        ]
        jarDirs.each { java.io.File jarDir ->
            if (jarDir.exists() && jarDir.isDirectory()) {
                jarDir.eachFileMatch(~/(?i).*\.jar$/) { java.io.File jarFile ->
                    gcl.addURL(jarFile.toURI().toURL())
                }
            }
        }

        def pathUtilsFile = [
            moduleDir.resolve('../lib/PathUtils.groovy').toFile(),
            moduleDir.resolve('../../lib/PathUtils.groovy').toFile()
        ].find { java.io.File candidateFile -> candidateFile.exists() }

        if (!pathUtilsFile) {
            throw new java.io.FileNotFoundException("[NATIVE_SPLIT] Missing PathUtils.groovy in lib tree.")
        }

        if (pathUtilsFile.parentFile.exists()) {
            gcl.addClasspath(pathUtilsFile.parentFile.absolutePath)
        }

        def lvl  = level_tag ? level_tag.toString().trim().toUpperCase() : "L1"
        target_id = target_id_val ? target_id_val.toString().trim() : params.project_name.toString().trim()

        def PathUtils = gcl.parseClass(pathUtilsFile)
        PathUtils.runSplit(moduleDir, task, params, context_path, full_pattern, lvl)
}