# iPhone client

The app, the broadcast extension and the keyboard. iOS 17 or later, Swift 5, system frameworks only. The project is generated from
`project.yml` with XcodeGen: targets `App`, `BroadcastExtension`, `KeyboardExtension` and the framework `Shared`. The generated
`App.xcodeproj` stays out of git. The network contract is [docs/protocol.md](../docs/protocol.md).

## Setup

Project and signing settings are in `Config/Project.xcconfig`: `APP_DISPLAY_NAME`, `BUNDLE_IDENTIFIER_PREFIX`,
`APP_GROUP_IDENTIFIER`, `DEFAULT_SERVER_URL`. Write a URL there as `https:/$()/…`, because `//` starts a comment.

`Config/Local.xcconfig` is optional and stays out of git. Its `DEFAULT_SERVER_TOKEN` goes to `DefaultServerToken` in the app's
Info.plist, and on launch the app moves it to the App Group if no token is there yet. Without the file the app has no token.

```sh
cd ios
xcodegen generate
open App.xcodeproj
```

## The call

1. The main screen works like a phone: one button to call and one to end. The app's own words on a call are in
   `Shared/Sources/CallWords.swift`, in Russian, English and Spanish by the language chosen in settings or else the phone's, English
   for any other. "⋯" opens the settings: the log, the frame, the language, the tone of the call and the voice.
2. A call creates a session, turns on `.playAndRecord`, says hello and opens the microphone. Right after the greeting the app asks
   iOS for the system broadcast sheet. If it does not open, the "Show your screen" button stays, with the real system button under it.
3. `CallMicrophone` listens until the end of the call, also from another app: iOS gives a background app the microphone only if it
   was turned on in the foreground. `SpeechDetector` tells speech from the noise of the room and ends a phrase after a 0.8 second
   pause. `SpeechStream` sends the sound to `/listen` while a voice is heard, and the server's stream finds the end of the phrase. If
   the server does not confirm it with `heard`, the phrase goes to `/say` as a file.
4. `ServerSpeaker` plays each line as it arrives from `POST /speech`, on `AVAudioEngine`. With no sound in time, `SystemSpeaker` reads
   the line in the system voice. The agent does not hear the person while it speaks and for 0.3 seconds after. The voice setting
   behind "⋯" lists the voices from `/voices` and works from the next line.
5. With no answer 1.5 seconds after a phrase, the phone says a short waiting phrase from `PauseWords`, in Russian calls only.
6. A `hint` event carries a 720×960 picture for the floating window: a zoomed part of the screen with the target outlined.
   `PictureInPictureHintWindow` draws it through `AVSampleBufferDisplayLayer`. A second window source, based on a video call, stays in
   the code with no switch on screen.
7. `paste_text` goes to `UIPasteboard` as soon as it arrives. iOS lets a background app reach the clipboard only while the window is
   open, so if the write fails, it is repeated when the person comes back. The keyboard road stays in the code with no setting on
   screen: with it the keyboard takes the text from `/suggestions` instead.
8. "Завершить" stops the voice, the microphone, the events and the window, turns off the audio session and sends `/end` under
   `beginBackgroundTask`. The extension notices the end through the App Group within 0.5 seconds and finishes the broadcast.

`HintEventConnection` keeps the WebSocket to `/events` with a ping every 10 seconds, and reconnects with a delay of 1 to 10 seconds.
While the socket is down it asks `/hint` once a second. A repeated `step_id` does not show the hint again.

## Broadcast extension

Video only, no sound. The settings are in `SampleHandler.Capture`: a look every 0.5 seconds, a long side of 1000 pixels, JPEG quality
0.6, and a 256×256 grey copy for comparing frames. Our own window is left out of the comparison: `WindowFinder` finds it by its
coloured border, so its changing picture does not count as a change. Comparison is in `ScreenStillness`. A frame goes to the server
when the screen has stopped changing, and it counts as a small change below 30% of the picture.

When the person speaks, the extension puts the screen as it is on `FrameShelf` in the App Group, however little it changed, and the
app sends it to `/look`.

There is no queue and no stored full frame: a sample is handled in place in an `autoreleasepool`. One frame request runs at a time. A
memory warning clears the cache, and a frame is skipped when less than 8 MB is left before the process limit.

## Screen text

`Shared.ScreenTextReader` reads words and their boxes with Apple Vision, and the server uses them to pick the exact box. Vision makes
no decisions. `screenReading` in the App Group picks where it runs, and nothing on screen changes it:

- `extension`, the default: `.accurate` Vision on the same small JPEG, in the extension, in a queue of its own. It is skipped when less
  than 16 MB of memory is available.
- `app`: the extension hands the frame over through `ScreenTextMailbox` in the App Group, and the app reads it and sends `/text`.
- `extensionFast`: the same as `extension` at the `.fast` level.
- `off`: no reading.

If a read fails, `ScreenTextWorker` repeats it once on the CPU. The shared frame is deleted at the start and the end of a call.

## Keyboard

The page that explains how to turn it on stays in the code, and settings no longer lead to it. The keyboard shows a hint, up to three chips with ready text,
"Перевести" (translate), the globe and delete. It has no full layout and no microphone. It polls `/suggestions` once a second, only
while it is on screen. Without full access it uses neither the network nor the App Group and asks for that access. Some apps do not
allow third-party keyboards, and there the clipboard does the job.

## Eyes

Always on. In a call, the window shows two eyes drawn in code between hints, the nose of the icon, a word under them, and between the
border and the eyes the drawing chosen in settings, from `EyesFrame`. `EyesDirector` decides how they move and `EyesStage` draws, up to
30 pictures a second in motion and none at rest. The word comes from real events of the call. On a payment, card or password screen
the eyes only rest. They shut only on the server's `not_looking`, when frames really stop.

## Checks

"Журнал" in settings shows the log and copies it. The log is `events.log` in the App Group, limited to about 256 KB. It holds facts and
timings, not what was said. The older checks screen stays in the code with no way to it.

From the repository root:

```sh
(cd ios && xcodegen generate)
xcodebuild -project ios/App.xcodeproj -scheme App \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath build/ CODE_SIGNING_ALLOWED=NO build
sh ios/scripts/check-protocol.sh
```

`check-protocol.sh` needs no server: `URLProtocol` stands in for the network. It checks requests and events against the protocol, the
settings, frame comparison, phrase detection and the movements of the eyes, with strict concurrency and warnings as errors.
`--vision` also runs text reading on a drawn picture.

In the Simulator a file, `tmp/heard.wav` in the app's data folder, stands in for the microphone. The broadcast, the floating window
and the keyboard flow need a real iPhone.
