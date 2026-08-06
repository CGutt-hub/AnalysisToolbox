nextflow.enable.dsl=2

include { FINALIZE_L2 } from '../processes/FINALIZE_L2.nf'

workflow finalize_l2 {
    take:
        root_l2_signals
        sync_trigger_signals

    main:
        def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
        gcl.addClasspath(moduleDir.resolve('../').toFile().absolutePath)

        def ChannelUtils = gcl.parseClass(moduleDir.resolve('../lib/ChannelUtils.groovy').toFile())

        def mixed_root
        if (root_l2_signals instanceof List) {
            def normRoot = root_l2_signals.collect { ch -> 
                ch.map { item -> item instanceof List || item.getClass().isArray() ? item[-1] : item } 
            }
            mixed_root = ChannelUtils.mixChannelList(normRoot).collect()
        } else {
            mixed_root = root_l2_signals.map { item -> item instanceof List || item.getClass().isArray() ? item[-1] : item }.collect()
        }

        def mixed_sync
        if (sync_trigger_signals instanceof List) {
            def normSync = sync_trigger_signals.collect { ch -> 
                ch.map { item -> item instanceof List || item.getClass().isArray() ? item[-1] : item } 
            }
            mixed_sync = ChannelUtils.mixChannelList(normSync).collect()
        } else {
            mixed_sync = sync_trigger_signals.map { item -> item instanceof List || item.getClass().isArray() ? item[-1] : item }.collect()
        }

        FINALIZE_L2( mixed_root, mixed_sync )
}