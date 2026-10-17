# MusicLens 모바일 버전 개발 타당성 검토

검토일: 2026-10-17. 목적은 Windows판의 핵심 경험인 **다른 앱에서 재생되는 음악을 자동 감지하고 곡 알림을 표시**하는 기능을 Android와 iOS에서 어느 정도 재현할 수 있는지 판단하는 것이다. 플랫폼 정책과 SDK는 바뀔 수 있으므로 구현 착수 시 최신 문서를 다시 확인해야 한다.

## 결론

- **Android: 조건부 개발 가능.** Android 10(API 29) 이상에서 사용자가 캡처를 승인하면 `AudioPlaybackCaptureConfiguration`과 `AudioRecord`로 캡처가 허용된 다른 앱의 미디어 재생음을 받을 수 있다. 모든 앱의 소리를 받을 수 있는 것은 아니다. 사용자가 시작하는 세션, 화면에 보이는 지속 알림, 앱별 캡처 허용 여부를 전제로 MVP를 설계하는 것이 현실적이다. [Android 재생음 캡처 안내](https://developer.android.com/media/platform/av-capture), [API 제한](https://developer.android.com/reference/android/media/AudioPlaybackCaptureConfiguration)
- **iOS: Windows와 같은 무인·상시 다른 앱 출력음 캡처 경험은 현재 확인한 공개 API만으로 설계 근거가 부족하다.** Apple은 ShazamKit의 마이크 입력 인식 경로를 공식 안내한다. ReplayKit은 앱 오디오·마이크의 화면 녹화/방송 기능으로 설명되므로, 이를 조용한 상시 시스템 루프백으로 가정해서는 안 된다. iOS MVP는 사용자가 누르는 마이크 기반 인식 또는 앱 자체가 재생하는 콘텐츠의 인식으로 범위를 바꾸는 것이 현실적이다. 이는 공개 문서의 기능 범위에 근거한 **설계 판단**이며, 모든 가능한 특수 권한의 부재를 증명하는 주장은 아니다. [Apple 마이크 인식 예제](https://developer.apple.com/documentation/shazamkit/matching-audio-using-the-built-in-microphone), [ReplayKit 개요](https://developer.apple.com/documentation/replaykit)

## Windows 코드의 재사용 범위

`app.py`의 Tk UI, pystray, PyAudioWPatch, Win32 mutex/event는 모바일에 직접 이식하지 않는다. **논리 정책**인 소리 감지 문턱값, 중복 알림 키, 인식 주기, 무음 건너뛰기, 오류 후 재시도 개념은 Kotlin/Swift로 다시 구현할 수 있다. `core.py`의 제목·아티스트 정리 방식과 링크 검증 규칙은 참고할 수 있지만 플랫폼 SDK의 결과 형식에 맞춰 변경해야 한다.

ShazamIO의 비공식 요청 코드를 모바일 앱에 그대로 넣는 방식은 권하지 않는다. Apple은 Android용 공식 ShazamKit AAR과 iOS ShazamKit을 제공하며, Android에서 Shazam 카탈로그를 사용하려면 Developer Token과 인터넷 접근이 필요하다고 문서화한다. 공식 경로를 선택해 인증, 배포 조건, 실패 처리를 설계하는 것이 적절하다. [ShazamKit](https://developer.apple.com/shazamkit/), [Android SDK 설명](https://developer.apple.com/shazamkit/android/)

## Android 설계안

### 가능한 흐름

1. 앱에서 사용자가 **인식 시작**을 누른다. Android의 `MediaProjectionManager.createScreenCaptureIntent()` 승인 화면을 보여 주고 `RECORD_AUDIO` 권한을 받는다. OS는 활성 캡처 세션을 사용자에게 표시하며 접근을 철회할 수 있다. [Android 캡처 안내](https://developer.android.com/media/platform/av-capture)
2. 승인된 `MediaProjection`으로 `AudioPlaybackCaptureConfiguration`을 만들고 `AudioRecord`에 적용한다. 미디어/게임 등 캡처 가능한 사용 유형만 포함한다. 다른 앱이 캡처를 허용해야 하며 같은 사용자 프로필이어야 한다. [캡처 가능 조건](https://developer.android.com/reference/android/media/AudioPlaybackCaptureConfiguration)
3. Kotlin 작업 흐름에서 PCM의 소리 활동을 감지하고, 허용된 오디오 구간을 공식 Android ShazamKit의 `SignatureGenerator`/`Session` 또는 `StreamingSession`으로 전달한다. SDK가 요구하는 16비트 모노 PCM 및 지원 샘플레이트에 맞춰 변환한다. 결과는 앱 내 화면과 Android 알림에 표시한다. [Android ShazamKit 예제](https://developer.apple.com/shazamkit/android/)
4. 사용자가 중지하거나 OS가 캡처 권한을 회수하면 `AudioRecord`와 세션을 정리한다. 긴 세션에는 해당 버전의 전경 서비스 유형과 권한을 선언하고 지속 알림을 제공한다. [전경 서비스 유형](https://developer.android.com/develop/background-work/services/fgs/service-types)

### 플랫폼 제한 및 출시 전 검증

- Android 10(API 29) 미만에는 다른 앱 재생음 캡처 API가 없다. ShazamKit Android SDK 자체의 최소 버전과 **캡처 기능의 최소 버전은 서로 다르다**. [캡처 API](https://developer.android.com/reference/android/media/AudioPlaybackCaptureConfiguration), [ShazamKit Android 요구 사항](https://developer.apple.com/shazamkit/android/)
- 재생 앱은 manifest 또는 플레이어 정책으로 캡처를 거부할 수 있다. YouTube, 브라우저, 스트리밍 앱별 결과를 실제 기기에서 확인해야 한다. 보호된 콘텐츠를 우회하는 설계는 범위에 넣지 않는다. [Android 캡처 제한](https://developer.android.com/media/platform/av-capture)
- Android 14 이상을 대상으로 하는 앱은 각 MediaProjection 캡처 세션에 새 사용자 동의를 받아야 한다. 따라서 Windows처럼 로그인 직후 조용히 자동 시작하는 UX를 목표로 두지 않는다. [Android 14 동의 규칙](https://developer.android.com/about/versions/14/behavior-changes-14)
- 장시간 캡처는 전경 서비스, 알림, 배터리 사용, 잠금 화면 및 블루투스 출력에서 검증이 필요하다. Play 배포 정책과 Apple Developer Token 제공 방식도 구현 전에 확정해야 한다.

### Android MVP 권장 범위

Android 10+에서 사용자가 시작·중지하는 재생음 인식 세션, 캡처 허용 앱에 대한 인식, 결과 알림, 중복 억제, 실패 상태 표시를 1차 목표로 한다. 테스트 기기는 Android 10/14 이상과 여러 제조사의 출력 장치 조합을 포함한다. 캡처가 막힌 앱은 **지원되지 않음**을 명확히 표시한다.

## iOS 설계안

### 가능한 범위

- **마이크 기반 음악 인식:** 사용자에게 마이크 사용 이유를 알리고 권한을 받은 뒤 ShazamKit `SHSession`에 오디오 버퍼를 공급한다. 주변에서 실제로 들리는 음악의 인식은 공식 예제로 구현 가능하다. 헤드폰 안에서만 재생되는 다른 앱의 소리는 마이크 방식으로 들리지 않을 수 있다. [Apple 공식 예제](https://developer.apple.com/documentation/shazamkit/matching-audio-using-the-built-in-microphone)
- **앱 자체 콘텐츠:** MusicLens가 직접 재생하는 음원 또는 사용자가 제공한 오디오 파일에 대해서는 앱이 버퍼를 확보해 ShazamKit에 전달할 수 있다. ShazamKit은 서명 또는 스트리밍 버퍼를 통한 검색을 지원한다. [SHSession](https://developer.apple.com/documentation/shazamkit/shsession)
- **ReplayKit 검토:** 사용자 주도 화면 방송/녹화가 사용 사례에 맞는지는 별도 실험이 필요하다. Apple의 공개 설명만으로 다른 앱의 모든 재생음을 무인·상시 캡처할 수 있다고 결론 내리지 않는다. [ReplayKit](https://developer.apple.com/documentation/replaykit)

### iOS MVP 권장 범위

수동 **지금 듣기**와 짧은 마이크 인식 세션, 결과 화면/알림, 중복 억제부터 시작한다. Windows판의 “백그라운드에서 다른 앱 시스템 출력을 자동 캡처”는 동일 기능 약속에서 제외한다. 사용자가 어떤 소리가 수집되는지 이해하고 중지할 수 있게 설계한다. [ShazamKit 사용 지침](https://developer.apple.com/design/human-interface-guidelines/shazamkit)

## 권장 구현 순서와 결정 조건

1. **제품 범위 선택:** 목표가 “휴대전화의 다른 앱 재생음을 자동 인식”이면 Android 우선. “주변 음악을 인식”이면 Android/iOS 동시 설계 가능.
2. **Android 기술 실험:** 실제 기기에서 재생 앱 3종 이상, 유선/블루투스 출력, 화면 잠금, 권한 회수, 30분 연속 세션을 확인한다. 재생 앱의 캡처 정책이 제품 가치에 가장 큰 불확실성이다.
3. **공식 인식 경로 검증:** ShazamKit Developer Token 발급과 모바일 카탈로그 인식 성공, 오류/호출 제한 처리, 배포 조건을 확인한다. 공식 SDK도 서비스 사용량과 실패 처리는 별도 확인이 필요하다.
4. **iOS 범위 확정:** 마이크 기반 MVP의 사용자 가치가 충분한지 확인한다. 다른 앱 출력음 수집은 공개 API의 실제 동작을 검증한 뒤에만 기능으로 약속한다.

**권고:** 먼저 Android 사용자 승인형 MVP를 만들고, iOS는 마이크 기반 별도 UX로 설계한다. Windows Python 앱을 패키징해 모바일로 옮기는 작업이 아니라 오디오 입력과 인식 SDK를 플랫폼별로 다시 구현하는 프로젝트다.
