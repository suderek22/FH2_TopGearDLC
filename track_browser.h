#pragma once
#include <algorithm>
#include <string>
#include <vector>

namespace TrackBrowser {
struct Item {
    int id = 0;
    std::string name, author, version, repository, reason, poster, logo;
    bool playable = false;
};
inline std::string Escape(const std::string& value) {
    std::string result;
    for (unsigned char c : value) {
        switch (c) {
        case '&': result += "&amp;"; break;
        case '<': result += "&lt;"; break;
        case '>': result += "&gt;"; break;
        case '"': result += "&quot;"; break;
        case '\'': result += "&apos;"; break;
        default: if (c >= 32) result += static_cast<char>(c);
        }
    }
    return result;
}
inline void Order(std::vector<Item>& items) {
    std::sort(items.begin(), items.end(), [](const Item& a, const Item& b) {
        if (a.id == 549 || b.id == 549) return a.id == 549 && b.id != 549;
        if (a.name != b.name) return a.name < b.name;
        return a.id < b.id;
    });
}
inline std::string Visible(int slot, int value) {
    return " Visibility=\"{Binding FH2Mod_Slot_" + std::to_string(slot) + "_Is_" +
        std::to_string(value) + ", Converter={StaticResource FH2Mod_ShowIf}}\"";
}
inline std::string Text(const std::string& value, int size = 25) {
    return "<local:FZTextBlock Style=\"{StaticResource A_Style_FZTextBlock_Body_Tight}\" "
        "TextWrapping=\"Wrap\" Text=\"" + Escape(value) + "\" FontSize=\"" +
        std::to_string(size) + "\" Color=\"{StaticResource A_Color_White}\" Margin=\"0,0,0,12\" />";
}
inline std::string Image(const std::string& path, int width, int height) {
    if (path.empty()) return Text("Artwork not supplied");
    return "<local:FZMaterial ImageSource=\"" + Escape(path) + "\" Width=\"" +
        std::to_string(width) + "\" Height=\"" + std::to_string(height) + "\" />";
}
inline std::string Resources() {
    return
        "<Style x:Key=\"TrackLoader_Thumbnail\" TargetType=\"{x:Type ListBoxItem}\">"
        "<Setter Property=\"Margin\" Value=\"4\" />"
        "<Setter Property=\"Template\"><Setter.Value><ControlTemplate TargetType=\"{x:Type ListBoxItem}\">"
        "<Border x:Name=\"Frame\" BorderThickness=\"0,0,0,8\" BorderBrush=\"#FF444444\" Background=\"#EE171717\" Padding=\"10\">"
        "<ContentPresenter /></Border><ControlTemplate.Triggers>"
        "<Trigger Property=\"IsSelected\" Value=\"True\"><Setter TargetName=\"Frame\" Property=\"BorderBrush\" Value=\"#FFFF541C\" /></Trigger>"
        "</ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>";
}
inline std::string Help() {
    const std::string buttons =
        " Style=\"{StaticResource A_Style_ContentControl_DefaultButtonBar}\""
        " local:ControllerButtonPanel.ADescription=\"Travel\""
        " local:ControllerButtonPanel.BDescription=\"{local:Loc HelpButtons.IDS_Back}\""
        " local:ControllerButtonPanel.DPadHorizontalDescription=\"Choose track\""
        " local:ControllerButtonPanel.LBumperDescription=\"Previous track\""
        " local:ControllerButtonPanel.RBumperDescription=\"Next track\"";
    return "<ContentControl" + Visible(1, 0) + buttons + " />"
        "<ContentControl" + Visible(1, 1) + buttons +
        " local:ControllerButtonPanel.YDescription=\"Return to mainland Airport\" />";
}
inline std::string Content(const std::vector<Item>& items) {
    std::string result = "<Grid Width=\"1500\" Height=\"710\" HorizontalAlignment=\"Center\">";
    for (size_t i = 0; i < items.size(); ++i) {
        const auto& item = items[i];
        result += "<Grid" + Visible(0, static_cast<int>(i)) + " VerticalAlignment=\"Top\" Height=\"385\">"
            "<Border HorizontalAlignment=\"Left\" Width=\"980\" Height=\"385\" Background=\"#EE171717\">" +
            Image(item.poster, 980, 365) + "</Border>"
            "<Border HorizontalAlignment=\"Right\" Width=\"500\" Height=\"385\" Background=\"#EE171717\" Padding=\"22,16\">"
            "<StackPanel>" +
            Text(item.name, 35) + Text("Track " + std::to_string(item.id)) +
            Text("Author: " + (item.author.empty() ? "Not specified" : item.author)) +
            Text("Version: " + (item.version.empty() ? "Not specified" : item.version)) +
            Text(item.repository.empty() ? "Updates: no GitHub repository supplied" : "Updates: not checked") +
            Text(item.playable ? "Available for travel" : item.reason, 21) +
            "</StackPanel></Border></Grid>";
    }
    result += "<local:CommonListBox Margin=\"0,395,0,80\" "
        "SelectedIndex=\"{Binding FH2Mod_Slot_0, Mode=TwoWay}\" "
        "KeyboardNavigation.DirectionalNavigation=\"Contained\" "
        "ScrollViewer.HorizontalScrollBarVisibility=\"Hidden\" ScrollViewer.VerticalScrollBarVisibility=\"Disabled\" "
        "ItemContainerStyle=\"{StaticResource TrackLoader_Thumbnail}\">"
        "<local:CommonListBox.ItemsPanel><ItemsPanelTemplate><StackPanel Orientation=\"Horizontal\" />"
        "</ItemsPanelTemplate></local:CommonListBox.ItemsPanel>";
    for (size_t i = 0; i < items.size(); ++i) {
        const auto& item = items[i];
        result += std::string("<ListBoxItem") + (i == 0 ? " x:Name=\"FirstItem\"" : "") +
            "><StackPanel Width=\"250\">" + Image(item.logo, 240, 148) +
            Text(item.name, 21) + "</StackPanel></ListBoxItem>";
    }
    result += "</local:CommonListBox><StackPanel VerticalAlignment=\"Bottom\">"
        "<Border Background=\"#EE171717\" Padding=\"14,8\"" + Visible(2, 1) + ">" +
        Text("Travel queued. Resuming driving automatically.", 21) + "</Border>"
        "<Border Background=\"#EE171717\" Padding=\"14,8\"" + Visible(2, 2) + ">" +
        Text("Travel unavailable. Return to Airport first, or wait for settled free roam. See platform log.", 21) + "</Border>"
        "<Border Background=\"#EE171717\" Padding=\"14,8\"" + Visible(2, 3) + ">" +
        Text("This track is not available for travel. See its details and the platform log.", 21) + "</Border>"
        "</StackPanel></Grid>";
    return result;
}
}
