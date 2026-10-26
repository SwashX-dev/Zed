/* Shown while Zed installs.  Every slide describes something that exists. */
import QtQuick 2.15
import calamares.slideshow 1.0

Presentation {
    id: presentation

    Timer {
        interval: 15000
        running: presentation.activatedInCalamares
        repeat: true
        onTriggered: presentation.goToNextSlide()
    }

    component ZedSlide: Slide {
        property string heading
        property string body
        Rectangle { anchors.fill: parent; color: "#0b0b0d" }
        Image {
            id: logo
            source: "zed-logo.png"
            width: 120; height: 120
            fillMode: Image.PreserveAspectFit
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: parent.height * 0.12
        }
        Text {
            id: title
            text: heading
            color: "#ffffff"
            font.pixelSize: 30; font.bold: true; font.letterSpacing: 2
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: logo.bottom; anchors.topMargin: 18
        }
        Rectangle {
            id: rule
            width: 90; height: 4; color: "#c41e3a"
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: title.bottom; anchors.topMargin: 10
        }
        Text {
            text: body
            color: "#c8c8ce"
            font.pixelSize: 16
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            width: parent.width * 0.7
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: rule.bottom; anchors.topMargin: 24
        }
    }

    ZedSlide {
        heading: "SUPERVISED BY s6"
        body: "Zed runs s6 and s6-rc instead of systemd. Every service starts in dependency order, has its own log and is restarted if it crashes."
    }
    ZedSlide {
        heading: "ONE COMMAND: zed"
        body: "zed install, zed remove, zed config, zed rollback. Every change is built first and only then activated; if activation fails, the previous system comes back automatically."
    }
    ZedSlide {
        heading: "EVERY CHANGE IS A GENERATION"
        body: "Each update creates a new system generation. Older generations stay in the boot menu, so you can always boot the last one that worked."
    }
    ZedSlide {
        heading: "YOURS TO EDIT"
        body: "Your own configuration lives in /etc/zed/host.nix and is never touched by Zed's tools. Apply your edits with: sudo zed apply"
    }
    ZedSlide {
        heading: "SECURE BY DEFAULT"
        body: "Firewall on, no listening services, no telemetry, no default passwords. Security tools are for systems you own or are authorised to test."
    }

    function onActivate() { presentation.currentSlide = 0; }
    function onLeave() { }
}
