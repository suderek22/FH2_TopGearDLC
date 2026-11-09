#include "../track_browser.h"
#include <cassert>
#include <fstream>

int main(int argc, char** argv) {
    using namespace TrackBrowser;
    std::vector<Item> items(3);
    items[0].id = 10001; items[0].name = "Z track";
    items[1].id = 10000; items[1].name = "A & <track> \"name\"";
    items[1].author = "A & B"; items[1].version = "1.0";
    items[1].poster = "/ForzaUI;component/Textures/Data_Bound/TrackLoader/track10000_large.png";
    items[1].logo = "/ForzaUI;component/Textures/Data_Bound/TrackLoader/track10000_small.png";
    items[1].playable = true;
    items[2].id = 549; items[2].name = "Storm Island";
    Order(items);
    assert(items[0].id == 549 && items[1].id == 10000 && items[2].id == 10001);
    const auto content = Content(items);
    assert(content.find("Storm Island") < content.find("A &amp; &lt;track&gt; &quot;name&quot;"));
    assert(content.find("Author: A &amp; B") != std::string::npos);
    assert(content.find("A: travel to this track") != std::string::npos);
    assert(content.find("FH2Mod_Slot_0_Is_2") != std::string::npos);
    assert(content.find("FH2Mod_Slot_1_Is_1") != std::string::npos);
    assert(content.find("x:Name=\"FirstItem\"") != std::string::npos);
    assert(Text("Font").find("Style=\"{StaticResource A_Style_FZTextBlock_Body_Tight}\"") != std::string::npos);
    assert(Text("Wrapped").find("TextWrapping=\"Wrap\"") != std::string::npos);
    assert(Text("Color").find("Color=\"{StaticResource A_Color_White}\"") != std::string::npos);
    assert(Escape("a\tb\nc") == "abc");
    if (argc > 1) {
        std::ofstream out(argv[1]);
        out << "<Root xmlns:x=\"http://schemas.microsoft.com/winfx/2006/xaml\" xmlns:local=\"ForzaUI\">"
            << Resources() << content << "</Root>";
        assert(out.good());
    }
}
