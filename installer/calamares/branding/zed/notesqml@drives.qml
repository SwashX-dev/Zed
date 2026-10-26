/* "Drives" page: how Linux names disks, and how to keep existing data safe. */
import QtQuick 2.15
import QtQuick.Controls 2.15

Item {
    ScrollView {
        anchors.fill: parent
        anchors.margins: 24
        clip: true

        Column {
            width: 780
            spacing: 14

            Text {
                text: "Drives and partitions"
                color: "#ffffff"; font.pixelSize: 24; font.bold: true
            }
            Rectangle { width: 70; height: 3; color: "#c41e3a" }

            Text {
                width: 760
                wrapMode: Text.WordWrap
                color: "#c8c8ce"
                font.pixelSize: 14
                textFormat: Text.RichText
                text: "<p><b>No drive letters.</b> Windows calls partitions <tt>C:</tt>, <tt>D:</tt>, <tt>E:</tt>. " +
                      "Linux names the disk and the partition instead, and shows a partition's files at a folder you choose (its <i>mount point</i>):</p>" +
                      "<table cellspacing=\"6\">" +
                      "<tr><td><tt>/dev/nvme0n1</tt></td><td>the first NVMe SSD</td></tr>" +
                      "<tr><td><tt>/dev/nvme0n1p3</tt></td><td>its third partition</td></tr>" +
                      "<tr><td><tt>/dev/sda</tt>, <tt>/dev/sdb</tt></td><td>SATA and USB drives</td></tr>" +
                      "<tr><td><b>Label</b></td><td>the name you gave the partition (Windows shows it next to the letter)</td></tr>" +
                      "</table>" +
                      "<p>The next page lists every disk with its partitions, file systems, sizes and labels. " +
                      "Windows <tt>C:</tt> is normally the largest <b>NTFS</b> partition on the disk that also has a small <b>EFI</b> (FAT32) partition.</p>" +
                      "<p><b>Keeping Windows or another Linux.</b> Choose <i>Install alongside</i> or <i>Replace a partition</i>. " +
                      "Other partitions are not changed, and the Zed boot menu lists the other system.</p>" +
                      "<p><b>Using an existing data drive</b> (for example Windows <tt>D:</tt>). Choose <i>Manual partitioning</i>, select the partition, " +
                      "click <i>Edit</i>, set a mount point such as <tt>/data/windows-d</tt> and leave <b>Format</b> unticked. " +
                      "Zed mounts it at every boot and shows it in the file manager; if the drive is missing, Zed still starts.</p>" +
                      "<p><b>Nothing is written to any disk until you confirm</b> the summary, which lists every partition that will be formatted or erased.</p>" +
                      "<p>Drives you do not set up here, such as USB sticks, appear in the file manager when plugged in.</p>"
            }
        }
    }
}
