import Foundation

/// One voice installed on the phone. Apart from iOS, so the choice can be checked.
struct VoiceChoice: Equatable {
    let id: String
    let language: String
    let quality: Int
    var isSpecial = false  // A novelty voice, or the person's own.

    /// The best one for the language of a call. Quality goes first, then the same accent, then the voice iOS itself would take.
    static func best(of voices: [VoiceChoice], for tag: String, usual: String?) -> VoiceChoice? {
        let wanted = plain(tag)
        return voices.filter { !$0.isSpecial && family($0.language) == family(wanted) }.max { one, other in
            let rank = { (voice: VoiceChoice) in (voice.quality, plain(voice.language) == wanted ? 1 : 0, voice.id == usual ? 1 : 0, voice.id) }
            return rank(one) < rank(other)
        }
    }

    private static func plain(_ tag: String) -> String { tag.lowercased().replacingOccurrences(of: "_", with: "-") }

    private static func family(_ tag: String) -> String { String(plain(tag).prefix { $0 != "-" }) }
}
