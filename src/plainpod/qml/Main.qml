import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

Kirigami.ApplicationWindow {
    id: root
    width: 1360
    height: 860
    minimumWidth: 1100 
    minimumHeight: 600 
    visible: true
    title: "PlainPod"

    onClosing: function(close) {
        close.accepted = false
        root.hide()
    }

    property int currentView: 0 // 0 Subscriptions, 1 Downloads, 2 Queue, 3 Settings
    property int activePodcastIndex: -1
    property int playingQueueIndex: 1

    function formatMillis(totalMs) {
        const seconds = Math.max(0, Math.floor(totalMs / 1000))
        const mins = Math.floor(seconds / 60)
        const secs = seconds % 60
        return `${mins}:${secs < 10 ? "0" + secs : secs}`
    }

    function switchView(index) {
        currentView = index
    }

    globalDrawer: Kirigami.GlobalDrawer {
        title: "PlainPod"
        modal: !wideScreen
        isMenu: !wideScreen
        handleVisible: !wideScreen
        leftPadding: Kirigami.Units.largeSpacing
        rightPadding: Kirigami.Units.largeSpacing

        header: ColumnLayout {
            width: parent.width
            spacing: Kirigami.Units.smallSpacing

            Label {
                text: "Your Podcasts"
                font.bold: true
                leftPadding: Kirigami.Units.smallSpacing
            }

            Button {
                text: "Add Podcast"
                icon.name: "list-add"
                Layout.fillWidth: true
                onClicked: addPodcastDialog.open()
            }
        }

        actions: [
            Kirigami.Action {
                text: "Subscriptions"
                icon.name: "view-list-details"
                checked: root.currentView === 0
                onTriggered: root.switchView(0)
            },
            Kirigami.Action {
                text: "Downloads"
                icon.name: "download"
                checked: root.currentView === 1
                onTriggered: root.switchView(1)
            },
            Kirigami.Action {
                text: "Queue"
                icon.name: "view-list-text"
                checked: root.currentView === 2
                onTriggered: root.switchView(2)
            },
            Kirigami.Action {
                text: "Settings"
                icon.name: "settings-configure"
                checked: root.currentView === 3
                onTriggered: root.switchView(3)
            }
        ]
    }

    Dialog {
        id: addPodcastDialog
        title: "Add Podcast"
        modal: true
        x: (root.width - width) / 2
        y: (root.height - height) / 2
        standardButtons: Dialog.Ok | Dialog.Cancel

        onAccepted: {
            if (feedInput.text.length > 0) {
                vm.add_feed(feedInput.text)
                feedInput.text = ""
            }
        }

        contentItem: ColumnLayout {
            spacing: Kirigami.Units.smallSpacing
            Label {
                text: "Paste RSS URL"
            }
            TextField {
                id: feedInput
                Layout.preferredWidth: 520
                placeholderText: "https://example.com/feed.xml"
                selectByMouse: true
                onAccepted: addPodcastDialog.accept()
            }
        }
    }

    Dialog {
        id: unsubscribeDialog
        title: "Unsubscribe"
        modal: true
        x: (root.width - width) / 2
        y: (root.height - height) / 2
        standardButtons: Dialog.Ok | Dialog.Cancel

        onAccepted: {
            if (vm.selected_podcast_id_value >= 0) {
                vm.remove_podcast(vm.selected_podcast_id_value)
            }
        }

        contentItem: Label {
            text: vm.selected_podcast_title.length > 0
                  ? `Unsubscribe from "${vm.selected_podcast_title}"?`
                  : "Unsubscribe from this podcast?"
            wrapMode: Text.Wrap
        }
    }

    Rectangle {
        id: contentArea
        anchors {
            top: parent.top
            left: parent.left
            right: parent.right
            bottom: mediaController.top
            margins: Kirigami.Units.largeSpacing
        }
        color: "transparent"

        StackLayout {
            anchors.fill: parent
            currentIndex: root.currentView

            Item {
                id: subscriptionsView

                SplitView {
                    anchors.fill: parent

                    Pane {
                        SplitView.preferredWidth: 350

                        ColumnLayout {
                            anchors.fill: parent
                            spacing: Kirigami.Units.smallSpacing

                            RowLayout {
                                Layout.fillWidth: true
                                Button {
                                    text: "Import OPML"
                                    icon.name: "document-import"
                                    onClicked: opml.import_file()
                                }
                                Button {
                                    text: "Refresh All"
                                    icon.name: "view-refresh"
                                    onClicked: vm.refresh_all_podcasts()
                                }
                                TextField {
                                    Layout.fillWidth: true
                                    placeholderText: "Filter subscriptions"
                                    onTextChanged: vm.set_subscription_filter(text)
                                }
                            }

                            ListView {
                                id: podcastList
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                spacing: Kirigami.Units.smallSpacing
                                model: vm.podcast_model
                                currentIndex: root.activePodcastIndex
                                onCountChanged: {
                                    if (count <= 0) {
                                        root.activePodcastIndex = -1
                                    } else if (root.activePodcastIndex >= count) {
                                        root.activePodcastIndex = count - 1
                                    }
                                }

                                delegate: ItemDelegate {
                                    required property int index
                                    required property int podcast_id
                                    required property string title
                                    required property string artwork_source
                                    required property string latest_episode_display
                                    required property int new_count
                                    required property bool is_stale
                                    required property string stale_label
                                    width: ListView.view.width
                                    padding: Kirigami.Units.smallSpacing
                                    highlighted: root.activePodcastIndex === index

                                    onClicked: {
                                        root.activePodcastIndex = index
                                        vm.select_podcast(podcast_id)
                                    }

                                    contentItem: RowLayout {
                                        spacing: Kirigami.Units.smallSpacing

                                        Rectangle {
                                            Layout.preferredWidth: 40
                                            Layout.preferredHeight: 40
                                            radius: 6 
                                            color: Kirigami.Theme.alternateBackgroundColor
                                            border.color: Kirigami.Theme.disabledTextColor
                                            clip: true 

                                            Image {
                                                anchors.fill: parent
                                                source: artwork_source
                                                fillMode: Image.PreserveAspectCrop
                                                cache: true
                                                visible: source.toString().length > 0
                                            }

                                            Kirigami.Icon {
                                                anchors.centerIn: parent
                                                source: "audio-podcast"
                                                width: 20
                                                height: 20
                                                visible: artwork_source.length === 0
                                                opacity: 0.5 
                                            }
                                        }

                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 2

                                            Label {
                                                Layout.fillWidth: true
                                                text: title
                                                elide: Text.ElideRight
                                                font.bold: root.activePodcastIndex === index
                                            }

                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: Kirigami.Units.smallSpacing
                                                Label {
                                                    text: latest_episode_display
                                                    opacity: 0.7
                                                    font.pointSize: 9
                                                    elide: Text.ElideRight
                                                }
                                                Label {
                                                    visible: new_count > 0
                                                    text: `${new_count} new`
                                                    color: Kirigami.Theme.positiveTextColor
                                                    font.pointSize: 9
                                                }
                                                Label {
                                                    visible: is_stale
                                                    text: stale_label
                                                    color: Kirigami.Theme.neutralTextColor
                                                    font.pointSize: 9
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Pane {
                        SplitView.fillWidth: true

                        ColumnLayout {
                            anchors.fill: parent
                            spacing: Kirigami.Units.smallSpacing

                            Kirigami.Card {
                                Layout.fillWidth: true

                                contentItem: RowLayout {
                                    anchors.fill: parent
                                    spacing: Kirigami.Units.largeSpacing

                                    Rectangle {
                                        Layout.preferredWidth: 116
                                        Layout.preferredHeight: 116
                                        radius: 12
                                        color: Kirigami.Theme.alternateBackgroundColor
                                        clip: true

                                        Image {
                                            anchors.fill: parent
                                            source: vm.selected_podcast_artwork_url
                                            fillMode: Image.PreserveAspectCrop
                                            cache: true
                                            visible: source.toString().length > 0
                                        }

                                        Kirigami.Icon {
                                            anchors.centerIn: parent
                                            source: "audio-podcast"
                                            width: 48
                                            height: 48
                                            visible: vm.selected_podcast_artwork_url.length === 0
                                        }
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true

                                        Label {
                                            text: vm.selected_podcast_title.length > 0 ? vm.selected_podcast_title : "Selected Podcast"
                                            font.pointSize: 14
                                            font.bold: true
                                            Layout.fillWidth: true      
                                            elide: Text.ElideRight
                                        }
                                        Label {
                                            text: vm.selected_podcast_site_url.length > 0 ? vm.selected_podcast_site_url : "No site URL"
                                            opacity: 0.7
                                            Layout.fillWidth: true      
                                            elide: Text.ElideRight
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            wrapMode: Text.Wrap
                                            text: vm.selected_podcast_description.length > 0
                                                ? vm.selected_podcast_description
                                                : "Select a podcast to view feed details."
                                            maximumLineCount: 3
                                            elide: Text.ElideRight
                                        }
                                    }

                                    RowLayout {
                                        Button {
                                            text: "Refresh Feed"
                                            icon.name: "view-refresh"
                                            onClicked: vm.refresh_selected()
                                        }
                                        ComboBox {
                                            id: podcastDownloadPolicyCombo
                                            model: [
                                                { label: "Manual", value: "ask" },
                                                { label: "Off", value: "off" },
                                                { label: "All new", value: "new_episodes" },
                                                { label: "Newest 1", value: "latest_1" },
                                                { label: "Newest 3", value: "latest_3" },
                                                { label: "Newest 5", value: "latest_5" },
                                                { label: "Newest 10", value: "latest_10" }
                                            ]
                                            textRole: "label"
                                            valueRole: "value"
                                            function syncFromViewModel() {
                                                const idx = indexOfValue(vm.selected_podcast_download_policy)
                                                currentIndex = idx >= 0 ? idx : 0
                                            }
                                            Component.onCompleted: syncFromViewModel()
                                            Connections {
                                                target: vm
                                                function onSelected_podcast_download_policy_changed() {
                                                    podcastDownloadPolicyCombo.syncFromViewModel()
                                                }
                                                function onSelected_podcast_id_changed() {
                                                    podcastDownloadPolicyCombo.syncFromViewModel()
                                                }
                                            }
                                            onActivated: vm.selected_podcast_download_policy = currentValue
                                        }
                                        Button {
                                            text: "Unsubscribe"
                                            icon.name: "list-remove"
                                            enabled: vm.selected_podcast_id_value >= 0
                                            onClicked: unsubscribeDialog.open()
                                        }
                                    }
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                TextField {
                                    Layout.fillWidth: true
                                    placeholderText: "Filter episodes"
                                    onTextChanged: vm.set_episode_filter(text)
                                }
                                ComboBox {
                                    model: ["Newest", "Oldest", "Duration (Longest)"]
                                    onActivated: vm.set_episode_sort(currentIndex)
                                }
                            }

                            ListView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                spacing: Kirigami.Units.smallSpacing
                                model: vm.episode_model

                                delegate: Kirigami.AbstractCard {
                                    required property int episode_id
                                    required property string title
                                    required property string published_display
                                    required property string duration
                                    required property bool has_progress
                                    required property string progress_display
                                    required property string local_path
                                    required property bool played
                                    required property bool is_new_since_launch
                                    required property bool is_unplayed
                                    required property bool is_in_progress
                                    required property string episode_badge_label
                                    width: ListView.view.width

                                    contentItem: RowLayout {
                                        spacing: Kirigami.Units.smallSpacing

                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 2

                                            Label {
                                                text: title
                                                font.bold: true
                                                elide: Text.ElideRight
                                                Layout.fillWidth: true
                                            }

                                            RowLayout {
                                                spacing: Kirigami.Units.smallSpacing

                                                Label {
                                                    text: published_display
                                                    opacity: 0.7
                                                }

                                                Label {
                                                    text: has_progress ? `${duration} • ${progress_display}` : duration
                                                    opacity: 0.7
                                                }
                                            }
                                        }


                                        RowLayout {
                                            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                                            spacing: Kirigami.Units.smallSpacing

                                            Rectangle {
                                                radius: 8
                                                visible: episode_badge_label.length > 0
                                                color: played ? "#3b7b44" : (is_in_progress ? "#7a3f9e" : (is_new_since_launch ? "#2f5f9e" : "#7a5c2e"))
                                                implicitWidth: badgeLabel.implicitWidth + 12
                                                implicitHeight: badgeLabel.implicitHeight + 4

                                                Label {
                                                    id: badgeLabel
                                                    anchors.centerIn: parent
                                                    text: episode_badge_label
                                                    color: "white"
                                                    font.pointSize: 9
                                                }
                                            }

                                            Button {
                                                text: "Play"
                                                Layout.preferredWidth: 72
                                                onClicked: vm.play_episode(episode_id)
                                            }

                                            Button {
                                                icon.name: "download"
                                                Layout.preferredWidth: 72
                                                enabled: local_path.trim().length === 0
                                                ToolTip.visible: hovered && !enabled
                                                ToolTip.text: "Already downloaded"
                                                onClicked: vm.download_episode(episode_id)
                                            }

                                            Button {
                                                icon.name: "list-add"
                                                Layout.preferredWidth: 72
                                                onClicked: vm.enqueue_episode(episode_id)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Pane {
                id: downloadsView
                ColumnLayout {
                    anchors.fill: parent
                    spacing: Kirigami.Units.smallSpacing

                    TextField {
                        Layout.fillWidth: true
                        placeholderText: "Filter downloads"
                        onTextChanged: vm.set_download_filter(text)
                    }
                    ComboBox {
                        model: ["Newest", "Oldest"]
                        onActivated: vm.set_episode_sort_downloads(currentIndex)
                    }
                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: vm.download_model
                        section.property: "section"
                        section.delegate: Rectangle {
                            width: ListView.view.width
                            height: 34
                            color: Kirigami.Theme.alternateBackgroundColor
                            Label {
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.left: parent.left
                                anchors.leftMargin: Kirigami.Units.smallSpacing
                                text: section
                                font.bold: true
                            }
                        }

                        delegate: Kirigami.AbstractCard {
                            required property string section
                            required property int episode_id
                            required property string title
                            required property string podcast_title
                            required property string progress_label
                            required property string speed_label
                            required property int progress_percent
                            required property string status
                            width: ListView.view.width

                            contentItem: ColumnLayout {
                                spacing: Kirigami.Units.smallSpacing

                                Label {
                                    text: title
                                    font.bold: true
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Label {
                                    text: podcast_title
                                    opacity: 0.65
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                    visible: podcast_title.length > 0
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        text: progress_label
                                        opacity: 0.7
                                    }
                                    Item { Layout.fillWidth: true }
                                    Label {
                                        text: speed_label
                                        opacity: 0.7
                                    }
                                }

                                ProgressBar {
                                    Layout.fillWidth: true
                                    from: 0
                                    to: 1
                                    value: progress_percent / 100.0
                                    visible: status === "downloading" || status === "paused"
                                }

                                RowLayout {
                                    Layout.alignment: Qt.AlignRight
                                    visible: status === "downloading" || status === "paused"
                                    Button {
                                        icon.name: status === "paused" ? "media-playback-start" : "media-playback-pause"
                                        onClicked: {
                                            if (status === "paused") {
                                                vm.resume_download(episode_id)
                                            } else {
                                                vm.pause_download(episode_id)
                                            }
                                        }
                                    }
                                    Button {
                                        icon.name: "dialog-cancel"
                                        onClicked: vm.cancel_download(episode_id)
                                    }
                                }

                                RowLayout {
                                    Layout.alignment: Qt.AlignRight
                                    visible: status === "completed" || status === "failed" || status === "canceled"
                                    Button {
                                        text: "Play"
                                        enabled: status === "completed"
                                        onClicked: vm.play_download(episode_id)
                                    }
                                    Button {
                                        icon.name: "list-add"
                                        onClicked: vm.enqueue_episode(episode_id)
                                    }
                                    Button {
                                        icon.name: "edit-delete"
                                        onClicked: vm.delete_download(episode_id)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Pane {
                id: queueView
                ColumnLayout {
                    anchors.fill: parent
                    spacing: Kirigami.Units.smallSpacing

                    RowLayout {
                        Layout.fillWidth: true
                        TextField {
                            Layout.fillWidth: true
                            placeholderText: "Filter queue"
                            onTextChanged: vm.set_queue_filter(text)
                        }
                        Button { text: "Clear Queue"; onClicked: vm.clear_queue() }
                        Button { text: "Clear Played Episodes" }
                    }

                    ListView {
                        id: queueList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: vm.queue_model
                        spacing: Kirigami.Units.smallSpacing

                        delegate: Kirigami.AbstractCard {
                            required property int index
                            required property int episode_id
                            required property string title
                            required property string duration
                            required property string podcast
                            required property bool now_playing
                            required property string podcast_artwork_source
                            width: ListView.view.width

                            background: Rectangle {
                                radius: 8
                                color: now_playing ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.15) : Kirigami.Theme.backgroundColor
                                border.color: now_playing ? Kirigami.Theme.highlightColor : Kirigami.Theme.disabledTextColor
                            }

                            contentItem: RowLayout {
                                spacing: Kirigami.Units.smallSpacing

                                RowLayout {
                                    spacing: 0
                                    ToolButton {
                                        icon.name: "go-up"
                                        text: ""
                                        enabled: index > 0
                                        onClicked: vm.move_queue_item(episode_id, index - 1)
                                    }
                                    ToolButton {
                                        icon.name: "go-down"
                                        text: ""
                                        enabled: index < queueList.count - 1
                                        onClicked: vm.move_queue_item(episode_id, index + 1)
                                    }
                                }

                                Item { Layout.fillWidth: true } 

                                RowLayout {
                                    spacing: Kirigami.Units.smallSpacing
                                    Layout.alignment: Qt.AlignHCenter
                                    
                                    // Prevents massive titles from destroying the centering or pushing buttons off-screen also fix this already implemented better  way in other layouts
                                    Layout.maximumWidth: queueList.width - 200 

                                    Rectangle {
                                        Layout.preferredWidth: 34
                                        Layout.preferredHeight: 34
                                        radius: 6 // Using the polished "squircle" look here too!
                                        color: Kirigami.Theme.alternateBackgroundColor
                                        clip: true

                                        Image {
                                            anchors.fill: parent
                                            source: podcast_artwork_source
                                            fillMode: Image.PreserveAspectCrop
                                            cache: true
                                            visible: source.toString().length > 0
                                        }

                                        Kirigami.Icon {
                                            anchors.centerIn: parent
                                            source: "audio-podcast"
                                            width: 18
                                            height: 18
                                            visible: podcast_artwork_source.length === 0
                                        }
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true 
                                        
                                        Label { 
                                            text: title
                                            font.bold: true
                                            elide: Text.ElideRight
                                            horizontalAlignment: Text.AlignHCenter // Centers text if it wraps/stretches
                                            Layout.fillWidth: true
                                        }
                                        Label { 
                                            text: `${podcast} • ${duration}`
                                            opacity: 0.7
                                            elide: Text.ElideRight
                                            horizontalAlignment: Text.AlignHCenter
                                            Layout.fillWidth: true
                                        }
                                    }

                                    Kirigami.Icon {
                                        source: now_playing ? "media-playback-start" : ""
                                        visible: now_playing
                                        width: 18
                                        height: 18
                                    }
                                }

                                Item { Layout.fillWidth: true } 
                                Button {
                                    text: "Play"
                                    onClicked: vm.play_episode(episode_id)
                                }
                                ToolButton {
                                    icon.name: "window-close"
                                    Layout.alignment: Qt.AlignRight
                                    onClicked: vm.remove_queue_item(episode_id)
                                }
                            }
                        }
                        moveDisplaced: Transition {
                            NumberAnimation { properties: "x,y"; duration: 160 }
                        }
                    }
                }
            }

            Pane {
                id: settingsView

                SplitView {
                    anchors.fill: parent

                    ListView {
                        id: settingsCategoryList
                        SplitView.preferredWidth: 220
                        model: ["General", "Playback", "Downloads", "Library / Data"]
                        currentIndex: 0
                        delegate: ItemDelegate {
                            required property string modelData
                            required property int index
                            width: ListView.view.width
                            text: modelData
                            highlighted: ListView.view.currentIndex === index
                            onClicked: ListView.view.currentIndex = index
                        }
                    }

                    Pane {
                        SplitView.fillWidth: true

                        StackLayout {
                            anchors.fill: parent
                            currentIndex: settingsCategoryList.currentIndex

                            Kirigami.FormLayout {
                                CheckBox {
                                    id: startAtLoginCheckBox
                                    Kirigami.FormData.label: ""
                                    text: "Start at login"
                                    checked: vm.startup_behavior
                                    onClicked: vm.set_startup_behavior_enabled(checked)

                                    Connections {
                                        target: vm
                                        function onStartup_behavior_changed() {
                                            startAtLoginCheckBox.checked = vm.startup_behavior
                                        }
                                    }
                                }
                                CheckBox {
                                    id: notificationsEnabledCheckBox
                                    Kirigami.FormData.label: ""
                                    text: "Show notifications for new episodes"
                                    checked: vm.notifications_enabled
                                    onClicked: vm.set_notifications_enabled(checked)

                                    Connections {
                                        target: vm
                                        function onNotifications_enabled_changed() {
                                            notificationsEnabledCheckBox.checked = vm.notifications_enabled
                                        }
                                    }
                                }
                                CheckBox {
                                    id: refreshFeedsOnStartupCheckBox
                                    Kirigami.FormData.label: ""
                                    text: "Refresh feeds on startup"
                                    checked: vm.refresh_feeds_on_startup
                                    onClicked: vm.set_refresh_feeds_on_startup_enabled(checked)

                                    Connections {
                                        target: vm
                                        function onRefresh_feeds_on_startup_changed() {
                                            refreshFeedsOnStartupCheckBox.checked = vm.refresh_feeds_on_startup
                                        }
                                    }
                                }
                                CheckBox {
                                    Kirigami.FormData.label: ""
                                    text: "Enable local sync server (restart required)"
                                    checked: vm.sync_server_enabled
                                    property bool initialized: false
                                    Component.onCompleted: initialized = true
                                    onToggled: {
                                        if (initialized && vm.sync_server_enabled !== checked) {
                                            vm.sync_server_enabled = checked
                                        }
                                    }
                                }
                                TextField {
                                    Kirigami.FormData.label: "Sync host"
                                    enabled: vm.sync_server_enabled
                                    visible: vm.sync_server_enabled
                                    text: vm.sync_server_host
                                    placeholderText: "127.0.0.1"
                                    onEditingFinished: vm.sync_server_host = text
                                }
                                SpinBox {
                                    Kirigami.FormData.label: "Sync port"
                                    enabled: vm.sync_server_enabled
                                    visible: vm.sync_server_enabled
                                    from: 1
                                    to: 65535
                                    value: vm.sync_server_port
                                    onValueModified: vm.sync_server_port = value
                                }
                                CheckBox {
                                    Kirigami.FormData.label: ""
                                    enabled: vm.sync_server_enabled
                                    visible: vm.sync_server_enabled
                                    text: "Require Basic Auth"
                                    checked: vm.sync_server_require_auth
                                    onClicked: vm.sync_server_require_auth = checked
                                }
                                TextField {
                                    Kirigami.FormData.label: "Sync username"
                                    enabled: vm.sync_server_enabled && vm.sync_server_require_auth
                                    visible: vm.sync_server_enabled && vm.sync_server_require_auth
                                    text: vm.sync_server_username
                                    placeholderText: "plainpod"
                                    onEditingFinished: vm.sync_server_username = text
                                }
                                Label {
                                    visible: vm.sync_server_enabled && vm.sync_server_require_auth
                                    wrapMode: Text.WordWrap
                                    text: "Set PLAINPOD_SYNC_PASSWORD or store the password in your Linux keyring. Basic Auth should not be exposed on a network without TLS or a reverse proxy."
                                }
                                Label {
                                    visible: vm.sync_server_enabled && vm.sync_server_host === "0.0.0.0"
                                    wrapMode: Text.WordWrap
                                    text: "Warning: 0.0.0.0 exposes local sync on all network interfaces."
                                }
                            }

                            Kirigami.FormLayout {
                                Slider {
                                    id: speedSlider
                                    Kirigami.FormData.label: "Default playback speed"
                                    from: 0.5
                                    to: 3.0
                                    value: vm.default_speed
                                    stepSize: 0.1
                                    onMoved: vm.default_speed = value
                                }
                                Label { text: `${speedSlider.value.toFixed(1)}x` }
                                SpinBox {
                                    Kirigami.FormData.label: "Rewind skip (sec)"
                                    from: 5
                                    to: 120
                                    value: vm.skip_back_seconds
                                    onValueModified: vm.skip_back_seconds = value
                                }
                                SpinBox {
                                    Kirigami.FormData.label: "Fast forward skip (sec)"
                                    from: 5
                                    to: 300
                                    value: vm.skip_forward_seconds
                                    onValueModified: vm.skip_forward_seconds = value
                                }
                            }

                            Kirigami.FormLayout {
                                RowLayout {
                                    Kirigami.FormData.label: "Download directory"
                                    Layout.fillWidth: true
                                    Label { Layout.fillWidth: true; text: vm.download_directory; elide: Text.ElideMiddle }
                                    Button { text: "Browse..."; onClicked: vm.browse_download_directory() }
                                }
                                SpinBox {
                                    Kirigami.FormData.label: "Max concurrent downloads"
                                    from: 1
                                    to: 10
                                    value: vm.max_concurrent_downloads
                                    onValueModified: vm.max_concurrent_downloads = value
                                }
                                ComboBox {
                                    Kirigami.FormData.label: "Default for new podcasts"
                                    model: [
                                        { label: "Manual", value: "ask" },
                                        { label: "Off", value: "off" },
                                        { label: "All new", value: "new_episodes" },
                                        { label: "Newest 1", value: "latest_1" },
                                        { label: "Newest 3", value: "latest_3" },
                                        { label: "Newest 5", value: "latest_5" },
                                        { label: "Newest 10", value: "latest_10" }
                                    ]
                                    textRole: "label"
                                    valueRole: "value"
                                    Component.onCompleted: {
                                        const idx = indexOfValue(vm.auto_download_policy)
                                        currentIndex = idx >= 0 ? idx : 0
                                    }
                                    onActivated: vm.auto_download_policy = currentValue
                                }
                            }

                            Kirigami.FormLayout {
                                RowLayout {
                                    Kirigami.FormData.label: "SQLite database"
                                    Layout.fillWidth: true
                                    Label { Layout.fillWidth: true; text: vm.database_path; elide: Text.ElideMiddle }
                                    Button { text: "Browse..."; onClicked: vm.browse_database_path() }
                                }
                                RowLayout {
                                    Kirigami.FormData.label: "OPML"
                                    Button { text: "Import OPML"; onClicked: opml.import_file() }
                                    Button { text: "Export OPML"; onClicked: opml.export_file() }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Rectangle {
        id: mediaController
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        height: 120
        color: Kirigami.Theme.alternateBackgroundColor
        border.color: Kirigami.Theme.disabledTextColor

        RowLayout {
            anchors.fill: parent
            anchors.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.largeSpacing

            RowLayout {
                spacing: Kirigami.Units.smallSpacing
                ToolButton {
                    icon.name: "media-skip-backward"
                    onClicked: vm.skip_back(vm.skip_back_seconds)
                }
                ToolButton {
                    icon.name: vm.is_playing ? "media-playback-pause" : "media-playback-start"
                    onClicked: vm.toggle_playback()
                }
                ToolButton {
                    icon.name: "media-skip-forward"
                    onClicked: vm.skip_forward(vm.skip_forward_seconds)
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing

                    Rectangle {
                        Layout.preferredWidth: 44
                        Layout.preferredHeight: 44
                        radius: 6
                        color: Kirigami.Theme.backgroundColor
                        Kirigami.Icon {
                            anchors.centerIn: parent
                            source: "audio-podcast"
                            width: 24
                            height: 24
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Label {
                            text: vm.now_playing_podcast.length > 0 ? vm.now_playing_podcast : "Podcast Title"
                            font.bold: true
                            elide: Text.ElideRight
                        }
                        Label {
                            text: vm.now_playing_title.length > 0 ? vm.now_playing_title : "Episode Title currently playing"
                            opacity: 0.7
                            elide: Text.ElideRight
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Label { text: root.formatMillis(vm.playback_position_ms) }
                    Slider {
                        id: playbackSlider
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(vm.playback_duration_ms, 1)
                        value: Math.min(vm.playback_position_ms, to)
                        onMoved: vm.seek(value)
                    }
                    Label { text: root.formatMillis(vm.playback_duration_ms) }
                }
            }

            RowLayout {
                spacing: Kirigami.Units.smallSpacing
                Kirigami.Icon { source: "audio-volume-high"; width: 18; height: 18 }
                Slider {
                    Layout.preferredWidth: 130
                    from: 0
                    to: 1.0
                    value: vm.volume
                    onMoved: vm.set_volume(value)
                }
                ComboBox {
                    model: [0.75, 1.0, 1.25, 1.5, 2.0]
                    currentIndex: Math.max(0, model.indexOf(Number(vm.playback_speed.toFixed(2))))
                    textRole: ""
                    delegate: ItemDelegate {
                        required property var modelData
                        text: `${Number(modelData).toFixed(2)}x`
                        width: parent.width
                    }
                    contentItem: Text {
                        text: `${Number(vm.playback_speed).toFixed(2)}x`
                        verticalAlignment: Text.AlignVCenter
                    }
                    onActivated: vm.set_playback_speed(Number(currentValue))
                }
                Button {
                    text: "Queue"
                    checkable: true
                    checked: root.currentView === 2
                    onClicked: root.switchView(2)
                }
            }
        }
    }

    footer: Label {
        id: statusLabel
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        
        // General padding
        padding: Kirigami.Units.smallSpacing
        
        // Override left padding to account for the sidebar width on desktop/widescreen
        leftPadding: root.globalDrawer.isMenu ? Kirigami.Units.smallSpacing : root.globalDrawer.width + Kirigami.Units.smallSpacing
        
        text: "Ready"
    }

    Connections {
        target: vm
        function onInfo(message) { statusLabel.text = message }
        function onError(message) { statusLabel.text = message }
    }
}
