"""Subtitle service for processing subtitle files."""

import re
import shutil
from pathlib import Path

from server.core.path_security import PathSecurityError, validate_media_path
from server.models.subtitle import (
    BatchSubtitleRenameResponse,
    SubtitleAssociateResponse,
    SubtitleFile,
    SubtitleLanguage,
    SubtitleRenameResult,
    SubtitleScanResponse,
    VideoSubtitleAssociation,
)

# Supported subtitle extensions
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}

# Supported video extensions (for association)
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".wmv", ".mov", ".flv", ".rmvb",
    ".ts", ".m2ts", ".webm", ".3gp", ".mpg", ".mpeg", ".vob",
}

# Language code mappings
LANGUAGE_MAPPINGS: dict[str, SubtitleLanguage] = {
    # Simplified Chinese
    "chs": SubtitleLanguage.CHS,
    "sc": SubtitleLanguage.CHS,
    "zh": SubtitleLanguage.CHS,
    "chi": SubtitleLanguage.CHS,
    "zho": SubtitleLanguage.CHS,
    "zh-cn": SubtitleLanguage.CHS,
    "zh-hans": SubtitleLanguage.CHS,
    "chinese": SubtitleLanguage.CHS,
    "简体": SubtitleLanguage.CHS,
    "简中": SubtitleLanguage.CHS,
    # Traditional Chinese
    "cht": SubtitleLanguage.CHT,
    "tc": SubtitleLanguage.CHT,
    "zh-tw": SubtitleLanguage.CHT,
    "zh-hk": SubtitleLanguage.CHT,
    "zh-hant": SubtitleLanguage.CHT,
    "繁体": SubtitleLanguage.CHT,
    "繁中": SubtitleLanguage.CHT,
    # English
    "eng": SubtitleLanguage.ENG,
    "en": SubtitleLanguage.ENG,
    "english": SubtitleLanguage.ENG,
    # Japanese
    "jpn": SubtitleLanguage.JPN,
    "ja": SubtitleLanguage.JPN,
    "jap": SubtitleLanguage.JPN,
    "japanese": SubtitleLanguage.JPN,
    "日语": SubtitleLanguage.JPN,
    # Korean
    "kor": SubtitleLanguage.KOR,
    "ko": SubtitleLanguage.KOR,
    "korean": SubtitleLanguage.KOR,
    "韩语": SubtitleLanguage.KOR,
}


class SubtitleService:
    """Service for subtitle file processing."""

    def scan_subtitles(self, folder_path: str) -> SubtitleScanResponse:
        """Scan a folder for subtitle files.

        Args:
            folder_path: Path to the folder to scan.

        Returns:
            Response with list of found subtitle files.
        """
        try:
            folder = validate_media_path(folder_path, must_exist=True)
        except PathSecurityError:
            return SubtitleScanResponse(subtitles=[], total=0)
        if not folder.exists() or not folder.is_dir():
            return SubtitleScanResponse(subtitles=[], total=0)

        subtitles = []
        for file_path in folder.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in SUBTITLE_EXTENSIONS:
                subtitle = self._parse_subtitle_file(file_path)
                subtitles.append(subtitle)

        return SubtitleScanResponse(subtitles=subtitles, total=len(subtitles))

    def associate_subtitles(
        self,
        folder_path: str,
        video_files: list[str] | None = None,
    ) -> SubtitleAssociateResponse:
        """Associate subtitle files with video files.

        Args:
            folder_path: Path to the folder.
            video_files: Optional list of video filenames to match.

        Returns:
            Response with video-subtitle associations.
        """
        try:
            folder = validate_media_path(folder_path, must_exist=True)
        except PathSecurityError:
            return SubtitleAssociateResponse(associations=[])
        if not folder.exists():
            return SubtitleAssociateResponse(associations=[])

        # Get all subtitles
        scan_result = self.scan_subtitles(folder_path)
        subtitles = scan_result.subtitles

        # Get video files
        if video_files is None:
            video_files = []
            for file_path in folder.rglob("*"):
                if file_path.is_file() and file_path.suffix.lower() in VIDEO_EXTENSIONS:
                    video_files.append(file_path.name)

        # Associate
        associations = []
        for video in video_files:
            video_path = folder / video
            video_stem = Path(video).stem
            matched_subs = []

            for sub in subtitles:
                # Get subtitle base name (without language tag)
                sub_base = self._get_base_name(sub.filename)
                if self._names_match(video_stem, sub_base):
                    # Update association
                    sub.associated_video = video
                    matched_subs.append(sub)

            associations.append(
                VideoSubtitleAssociation(
                    video=video,
                    video_path=str(video_path),
                    subtitles=matched_subs,
                )
            )

        return SubtitleAssociateResponse(associations=associations)

    def rename_subtitle(
        self,
        subtitle_path: str,
        new_video_name: str,
        preserve_language: bool = True,
    ) -> SubtitleRenameResult:
        """Rename a subtitle file to match a video file.

        Args:
            subtitle_path: Path to the subtitle file.
            new_video_name: New video filename (without extension).
            preserve_language: Whether to preserve language tag.

        Returns:
            Result of the rename operation.
        """
        try:
            source = validate_media_path(
                subtitle_path,
                must_exist=True,
                require_file=True,
            )
        except PathSecurityError as exc:
            return SubtitleRenameResult(
                source_path=subtitle_path,
                dest_path="",
                success=False,
                error=str(exc),
            )

        if source.suffix.lower() not in SUBTITLE_EXTENSIONS:
            return SubtitleRenameResult(
                source_path=subtitle_path,
                dest_path="",
                success=False,
                error=f"Unsupported subtitle file: {subtitle_path}",
            )

        # Parse original subtitle
        subtitle_info = self._parse_subtitle_file(source)

        # Build new filename
        new_filename = new_video_name
        if preserve_language and subtitle_info.language:
            new_filename = f"{new_video_name}.{subtitle_info.language.value}"
        new_filename = f"{new_filename}{subtitle_info.extension}"

        dest = source.parent / new_filename
        try:
            dest = validate_media_path(str(dest))
        except PathSecurityError as exc:
            return SubtitleRenameResult(
                source_path=subtitle_path,
                dest_path=str(dest),
                success=False,
                error=str(exc),
            )

        # Check if destination exists
        if dest.exists() and dest != source:
            return SubtitleRenameResult(
                source_path=subtitle_path,
                dest_path=str(dest),
                success=False,
                error=f"Destination file already exists: {dest}",
            )

        try:
            shutil.move(str(source), str(dest))
            return SubtitleRenameResult(
                source_path=subtitle_path,
                dest_path=str(dest),
                success=True,
            )
        except OSError as e:
            return SubtitleRenameResult(
                source_path=subtitle_path,
                dest_path=str(dest),
                success=False,
                error=f"Rename failed: {e}",
            )

    def batch_rename_subtitles(
        self,
        items: list[tuple[str, str, bool]],
    ) -> BatchSubtitleRenameResponse:
        """Batch rename subtitle files.

        Args:
            items: List of (subtitle_path, new_video_name, preserve_language).

        Returns:
            Batch rename response.
        """
        results = []
        for subtitle_path, new_video_name, preserve_language in items:
            result = self.rename_subtitle(subtitle_path, new_video_name, preserve_language)
            results.append(result)

        success_count = sum(1 for r in results if r.success)
        return BatchSubtitleRenameResponse(
            total=len(results),
            success=success_count,
            failed=len(results) - success_count,
            results=results,
        )

    def _parse_subtitle_file(self, file_path: Path) -> SubtitleFile:
        """Parse a subtitle file path into SubtitleFile model.

        Args:
            file_path: Path to the subtitle file.

        Returns:
            SubtitleFile with parsed information.
        """
        filename = file_path.name
        extension = file_path.suffix.lower()
        language = self._extract_language(filename)

        return SubtitleFile(
            path=str(file_path),
            filename=filename,
            extension=extension,
            language=language,
        )

    def _extract_language(self, filename: str) -> SubtitleLanguage | None:
        """Extract language tag from filename.

        Args:
            filename: Subtitle filename.

        Returns:
            Detected language or None.
        """
        # Remove extension
        name = Path(filename).stem

        # Check for language tag at end (e.g., "video.chs" or "video.eng")
        parts = name.split(".")
        if len(parts) > 1:
            last_part = parts[-1].lower()
            if last_part in LANGUAGE_MAPPINGS:
                return LANGUAGE_MAPPINGS[last_part]

        # Check for language in brackets or parentheses
        patterns = [
            r"\[([^\]]+)\]",  # [chs]
            r"\(([^\)]+)\)",  # (chs)
            r"\.([a-zA-Z]{2,3})$",  # .chs
        ]

        for pattern in patterns:
            matches = re.findall(pattern, name, re.IGNORECASE)
            for match in matches:
                match_lower = match.lower()
                if match_lower in LANGUAGE_MAPPINGS:
                    return LANGUAGE_MAPPINGS[match_lower]

        return None

    def _get_base_name(self, filename: str) -> str:
        """Get base name of subtitle without language tag and extension.

        Args:
            filename: Subtitle filename.

        Returns:
            Base name for matching.
        """
        # Remove extension
        name = Path(filename).stem

        # Remove language tag if present
        parts = name.split(".")
        if len(parts) > 1:
            last_part = parts[-1].lower()
            if last_part in LANGUAGE_MAPPINGS:
                return ".".join(parts[:-1])

        return name

    def _names_match(self, video_name: str, subtitle_base: str) -> bool:
        """Check if video name matches subtitle base name.

        Args:
            video_name: Video filename stem.
            subtitle_base: Subtitle base name (without language/extension).

        Returns:
            True if names match.
        """
        # Exact match
        if video_name.lower() == subtitle_base.lower():
            return True

        # Normalize for comparison (remove common variations)
        def normalize(s: str) -> str:
            s = s.lower()
            s = re.sub(r"[\s\._-]+", "", s)
            return s

        return normalize(video_name) == normalize(subtitle_base)
