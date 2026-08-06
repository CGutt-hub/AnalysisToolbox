package lib

class ChannelUtils {

    static Object mixChannelList(List channelList) {
        if (!channelList) return null
        def mixed = channelList[0].map { tuple_item -> tuple_item }
        (1..<channelList.size()).each { idx ->
            mixed = mixed.mix(channelList[idx].map { tuple_item -> tuple_item })
        }
        return mixed
    }
}