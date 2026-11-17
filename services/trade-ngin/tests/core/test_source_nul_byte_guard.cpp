// tests/core/test_source_nul_byte_guard.cpp
//
// THIS TEST READS SOURCE, on purpose: it is a guard, not a behaviour test.
//
// One NUL byte makes git treat a whole file as binary. `git grep -n` then prints "Binary file ...
// matches" in place of the lines, a diff and a pull request show a byte count and no lines, a
// merge of the file is all or nothing, and `* text=auto eol=lf` stops normalising its line
// endings. src/live/qt_desk.cpp was in that state from 79b7e655 on: `buf[n] = '\0';` went in with
// the byte itself between the quotes, and every line of the file went in with a CRLF ending
// beside it. The byte compiles to the same value as the escape, so no behaviour test can see it,
// and the compiler's "null character(s) preserved in literal" is one warning in a long build log.
//
// The guard reads every source file under src/, include/, apps/ and tests/ and fails on a NUL
// byte, naming the file, the line and the column. The fix is to write the escape: '\0', "\0".
//
// It finds the tree through TRADE_NGIN_SOURCE_DIR, which tests/CMakeLists.txt sets to the service
// root, not by walking up from the working directory. A directory that is missing or holds no
// source file is a FAILURE, not a skip: a guard that looked at nothing has shown nothing.
//
// What it cannot see: it reads the files on disk, not git's index. So it takes a file by its
// extension (kSourceExtensions), it reads an untracked file that has one of them, and it does not
// read a tracked file that has another. It does not check line endings: `.gitattributes` does
// that for a file git takes as text, and the NUL byte is what stopped it.
//
// The last two tests hand the scanner a tree with nothing to read and a tree with the byte
// planted in it, and require it to report both.

#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <random>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#ifndef TRADE_NGIN_SOURCE_DIR
#error "TRADE_NGIN_SOURCE_DIR is not defined: tests/CMakeLists.txt sets it to the service root"
#endif

namespace {

namespace fs = std::filesystem;

// The directories of the service whose files are written by hand.
constexpr std::array<const char*, 4> kSourceDirs = {"src", "include", "apps", "tests"};

// A file is source by its extension: every extension tracked under those directories today, and
// the C and C++ ones a new file is likely to carry.
constexpr std::array<std::string_view, 10> kSourceExtensions = {
    ".c", ".cc", ".cpp", ".h", ".hpp", ".json", ".md", ".py", ".sql", ".txt"};

bool is_source(const fs::path& file) {
    const std::string ext = file.extension().string();
    return std::find(kSourceExtensions.begin(), kSourceExtensions.end(), ext) !=
           kSourceExtensions.end();
}

struct Scan {
    std::size_t files = 0;              ///< source files read
    std::vector<std::string> problems;  ///< one line per finding; empty when the tree is clean
};

// Reads every source file under the four directories of `root`. A directory that is missing or
// holds no source file is a problem like a NUL byte is: otherwise a scan of nothing reads as a
// clean one.
Scan scan_for_nul_bytes(const fs::path& root) {
    Scan scan;
    for (const char* dir : kSourceDirs) {
        const fs::path top = root / dir;
        if (!fs::is_directory(top)) {
            scan.problems.push_back(std::string(dir) + "/ not found under " + root.string());
            continue;
        }
        std::size_t in_dir = 0;
        for (const auto& entry : fs::recursive_directory_iterator(top)) {
            if (!entry.is_regular_file() || !is_source(entry.path())) continue;
            ++in_dir;
            const std::string name = fs::relative(entry.path(), root).generic_string();
            std::ifstream in(entry.path(), std::ios::binary);
            if (!in) {
                scan.problems.push_back(name + ": cannot be read");
                continue;
            }
            const std::string bytes((std::istreambuf_iterator<char>(in)),
                                    std::istreambuf_iterator<char>());
            const std::size_t at = bytes.find('\0');
            if (at == std::string::npos) continue;
            const auto first = bytes.begin() + static_cast<std::ptrdiff_t>(at);
            const auto line = 1 + std::count(bytes.begin(), first, '\n');
            const std::size_t line_start = bytes.rfind('\n', at);
            const std::size_t column =
                at - (line_start == std::string::npos ? 0 : line_start + 1) + 1;
            scan.problems.push_back(name + ":" + std::to_string(line) + ":" +
                                    std::to_string(column) + ": NUL byte (" +
                                    std::to_string(std::count(first, bytes.end(), '\0')) +
                                    " in the file)");
        }
        if (in_dir == 0) {
            scan.problems.push_back(std::string(dir) + "/ holds no source file under " +
                                    root.string());
        }
        scan.files += in_dir;
    }
    return scan;
}

std::string listed(const std::vector<std::string>& problems) {
    std::string out;
    for (const auto& problem : problems) out += "\n  " + problem;
    return out;
}

// A scratch tree: the four directories with one clean source file in each. Removed with the
// object.
struct ScratchTree {
    fs::path root = fs::temp_directory_path() /
                    ("tn_nul_byte_guard_" + std::to_string(std::random_device{}()));

    ScratchTree() {
        for (const char* dir : kSourceDirs) write(std::string(dir) + "/clean.cpp", "int clean;\n");
    }
    ~ScratchTree() {
        std::error_code ignored;
        fs::remove_all(root, ignored);
    }
    ScratchTree(const ScratchTree&) = delete;
    ScratchTree& operator=(const ScratchTree&) = delete;

    void write(const std::string& name, const std::string& bytes) const {
        fs::create_directories((root / name).parent_path());
        std::ofstream out(root / name, std::ios::binary);
        out.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    }
};

}  // namespace

TEST(SourceNulByteGuard, NoSourceFileHoldsANulByte) {
    const Scan scan = scan_for_nul_bytes(fs::path(TRADE_NGIN_SOURCE_DIR));
    RecordProperty("source_files_read", std::to_string(scan.files));

    EXPECT_TRUE(scan.problems.empty())
        << "A NUL byte makes git treat the whole file as binary: `git grep -n` prints \"Binary "
           "file ... matches\", and a diff or a pull request shows no lines. Write the escape "
           "('\\0', \"\\0\") in place of the byte. A directory named below was not found or "
           "held no source file, so nothing in it was checked."
        << listed(scan.problems);
}

// The guard must not pass because it found nothing to read.
TEST(SourceNulByteGuard, ADirectoryThatIsMissingOrHoldsNoSourceIsReported) {
    const ScratchTree tree;
    const Scan whole = scan_for_nul_bytes(tree.root);
    ASSERT_TRUE(whole.problems.empty()) << listed(whole.problems);
    ASSERT_EQ(whole.files, kSourceDirs.size());

    const Scan nowhere = scan_for_nul_bytes(tree.root / "no_checkout_here");
    EXPECT_EQ(nowhere.files, 0u);
    EXPECT_EQ(nowhere.problems.size(), kSourceDirs.size()) << listed(nowhere.problems);

    fs::remove(tree.root / "apps" / "clean.cpp");
    tree.write("apps/image.bin", "not source by its extension");
    const Scan hollow = scan_for_nul_bytes(tree.root);
    ASSERT_EQ(hollow.problems.size(), 1u) << listed(hollow.problems);
    EXPECT_EQ(hollow.problems.front().rfind("apps/ holds no source file", 0), 0u)
        << hollow.problems.front();
}

// The scanner has to be able to see the byte, or its silence means nothing.
TEST(SourceNulByteGuard, APlantedNulByteIsReportedWithItsFileLineAndColumn) {
    const ScratchTree tree;

    // The line qt_desk.cpp carried, as the third line of a file. The byte is appended at run
    // time: this file must not hold one.
    std::string planted = "char buf[8];\nvoid f(int n) {\n    buf[n] = '";
    planted.push_back('\0');
    planted += "';\r\n}\n";
    tree.write("src/live/planted.cpp", planted);
    // The same bytes in a file that is not source by its extension are not this guard's business.
    tree.write("tests/fixture.bin", planted);

    const Scan scan = scan_for_nul_bytes(tree.root);
    EXPECT_EQ(scan.files, kSourceDirs.size() + 1);
    ASSERT_EQ(scan.problems.size(), 1u) << listed(scan.problems);
    EXPECT_EQ(scan.problems.front(), "src/live/planted.cpp:3:15: NUL byte (1 in the file)");
}
