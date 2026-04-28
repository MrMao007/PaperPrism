# Homebrew formula template for the PaperPrism Agent.
#
# How to publish:
#   1. Create a tap repo, e.g. github.com/<owner>/homebrew-paperprism
#   2. Copy this file to Formula/paperprism-agent.rb in that repo
#   3. Update `url` + `sha256` + `version` after each GitHub Release
#      (the release.yml workflow prints the SHA256 of each tarball).
#
# User install flow:
#   brew tap <owner>/paperprism
#   brew install paperprism-agent
#
# The formula installs the single-file binary into Homebrew's prefix and
# wires up a LaunchAgent via `brew services start paperprism-agent`.

class PaperprismAgent < Formula
  desc "Local agent for PaperPrism: ingests arxiv PDFs into a hidden vault"
  homepage "https://github.com/paperprism/PaperPrism"
  version "0.1.0"
  license "MIT"

  on_macos do
    # We publish only an arm64 build for macOS; Intel Macs run it via
    # Rosetta 2 (Homebrew automatically downloads arm64 bottles on Intel
    # when an on_intel branch is absent, but we leave an explicit fallback
    # below for clarity).
    on_arm do
      url "https://github.com/paperprism/PaperPrism/releases/download/v#{version}/paperprism-agent-macos-arm64.tar.gz"
      sha256 "REPLACE_WITH_SHA256_FROM_RELEASE_ASSET"
    end
    on_intel do
      # Run the arm64 binary under Rosetta 2. Users may need:
      #   softwareupdate --install-rosetta --agree-to-license
      url "https://github.com/paperprism/PaperPrism/releases/download/v#{version}/paperprism-agent-macos-arm64.tar.gz"
      sha256 "REPLACE_WITH_SHA256_FROM_RELEASE_ASSET"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/paperprism/PaperPrism/releases/download/v#{version}/paperprism-agent-linux-x86_64.tar.gz"
      sha256 "REPLACE_WITH_SHA256_FROM_RELEASE_ASSET"
    end
    on_arm do
      url "https://github.com/paperprism/PaperPrism/releases/download/v#{version}/paperprism-agent-linux-arm64.tar.gz"
      sha256 "REPLACE_WITH_SHA256_FROM_RELEASE_ASSET"
    end
  end

  def install
    bin.install "paperprism-agent"
  end

  # `brew services start paperprism-agent` -> launchd LaunchAgent (macOS)
  # or systemd --user service (Linux, brew services v2.x).
  service do
    run [opt_bin/"paperprism-agent", "serve"]
    keep_alive true
    log_path var/"log/paperprism-agent.log"
    error_log_path var/"log/paperprism-agent.err.log"
    working_dir HOMEBREW_PREFIX
  end

  test do
    assert_match "paperprism", shell_output("#{bin}/paperprism-agent version")
  end
end
