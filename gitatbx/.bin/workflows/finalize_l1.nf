include { FINALIZE_L1 } from '../processes/FINALIZE_L1.nf'

workflow finalize_l1 {
    take:
        root_l1_signals
        sync_trigger_signals
        participant_context

    main:
        def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
        gcl.addClasspath(moduleDir.resolve('../').toFile().absolutePath)

        def ChannelUtils = gcl.parseClass(moduleDir.resolve('../lib/ChannelUtils.groovy').toFile())

        def mixed_root
        if (root_l1_signals instanceof List) {
            def countRoot = root_l1_signals.size()
            def normRoot  = root_l1_signals.collect { ch -> ch.map { item -> tuple(item[0].toString(), item[-1]) } }
            mixed_root    = ChannelUtils.mixChannelList(normRoot).groupTuple(by: 0, size: countRoot)
        } else {
            mixed_root    = root_l1_signals.map { item -> tuple(item[0].toString(), [item[-1]]) }
        }

        def mixed_sync
        if (sync_trigger_signals instanceof List) {
            def countSync = sync_trigger_signals.size()
            def normSync  = sync_trigger_signals.collect { ch -> ch.map { item -> tuple(item[0].toString(), item[-1]) } }
            mixed_sync    = ChannelUtils.mixChannelList(normSync).groupTuple(by: 0, size: countSync)
        } else {
            mixed_sync    = sync_trigger_signals.map { item -> tuple(item[0].toString(), [item[-1]]) }
        }

        unified = participant_context
            .map { pid, _raw, _folder -> tuple(pid.toString()) }
            .join(mixed_root, by: 0, remainder: false)
            .join(mixed_sync, by: 0, remainder: false)
            .map { pid, rootSigs, syncSigs ->
                def cleanRoot = (rootSigs instanceof List) ? rootSigs.flatten() : [rootSigs]
                def cleanSync = (syncSigs instanceof List) ? syncSigs.flatten() : [syncSigs]
                return tuple(pid, cleanRoot, cleanSync)
            }

        FINALIZE_L1( unified )

    emit:
        FINALIZE_L1.out
}