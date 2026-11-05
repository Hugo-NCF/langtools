from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from ..annotation.commands import InsertPoint, MoveBoundary, MovePoint, SetLabel, SplitInterval
from ..annotation.session import AnnotationSession
from ..errors import InvalidInput
from ..io.audio import AudioFile, signature
from ..io.textgrid import read, write
from ..model.textgrid import TextGrid


@dataclass(frozen=True)
class CommandSpec:
    id: str
    factory: type
    description: str


ANNOTATION_COMMANDS = {
    spec.id: spec
    for spec in (
        CommandSpec("annotation.set_label", SetLabel, "Set literal Unicode label text."),
        CommandSpec("annotation.split_interval", SplitInterval, "Split at a collapsed text caret."),
        CommandSpec("annotation.insert_point", InsertPoint, "Insert an ordered labeled point."),
        CommandSpec("annotation.move_boundary", MoveBoundary, "Move a shared internal endpoint."),
        CommandSpec(
            "annotation.move_point", MovePoint, "Move a point, retaining identity and sorted order."
        ),
    )
}


class Workspace:
    def __init__(self):
        self.objects: dict[str, AudioFile | AnnotationSession] = {}
        self.paths: dict[str, Path] = {}
        self._saved_signatures: dict[str, tuple[int, int, int, int]] = {}

    def open(self, path: Path) -> str:
        path = Path(path)
        if path.suffix.lower() == ".textgrid":
            before = signature(path)
            document = read(path)
            if signature(path) != before:
                raise InvalidInput("TextGrid changed while opening; reopen it.")
            self.objects[document.id] = AnnotationSession(document)
            self.paths[document.id] = path.resolve()
            self._saved_signatures[document.id] = before
            return document.id
        source = AudioFile.open(path)
        self.objects[source.id] = source
        return source.id

    def get(self, object_id: str) -> AudioFile | AnnotationSession:
        try:
            return self.objects[object_id]
        except KeyError as error:
            raise InvalidInput("Workspace object no longer exists.") from error

    def add_annotation(self, document: TextGrid) -> str:
        if document.id in self.objects:
            raise InvalidInput("Object ID already exists in the workspace.")
        self.objects[document.id] = AnnotationSession(document)
        self.objects[document.id].mark_unsaved()
        return document.id

    def execute(self, object_id: str, command_id: str, *, expected_revision: int, **parameters):
        target = self.get(object_id)
        if not isinstance(target, AnnotationSession):
            raise InvalidInput("Annotation commands require a TextGrid object.")
        try:
            spec = ANNOTATION_COMMANDS[command_id]
        except KeyError as error:
            raise InvalidInput(f"Unsupported command: {command_id}") from error
        try:
            command = spec.factory(**parameters)
        except TypeError as error:
            raise InvalidInput(f"Invalid parameters for {command_id}: {error}") from error
        return target.apply(command, expected_revision=expected_revision)

    def save(self, object_id: str, path: Path, *, overwrite: bool = False, short: bool = False):
        target = self.get(object_id)
        if not isinstance(target, AnnotationSession):
            raise InvalidInput("Only TextGrid objects have annotation files.")
        path = Path(path).resolve()
        if self.paths.get(object_id) == path:
            try:
                unchanged = signature(path) == self._saved_signatures.get(object_id)
            except FileNotFoundError:
                unchanged = False
            if not unchanged:
                raise InvalidInput(
                    "TextGrid changed on disk; reopen it or save to a different path."
                )
        write(path, target.document, overwrite=overwrite, short=short)
        self.paths[object_id] = path
        self._saved_signatures[object_id] = signature(path)
        target.mark_saved()

    def rename(self, object_id: str, name: str) -> None:
        if not name.strip():
            raise InvalidInput("Name must not be blank.")
        target = self.get(object_id)
        if isinstance(target, AudioFile):
            self.objects[object_id] = replace(target, name=name)
        else:
            # Names are workspace metadata, not encoded TextGrid document content.
            target.rename(name)

    def copy(self, object_id: str) -> str:
        target = self.get(object_id)
        new_id = str(uuid4())
        if isinstance(target, AudioFile):
            self.objects[new_id] = replace(target, id=new_id, name=f"{target.name} copy")
        else:
            document = replace(target.document, id=new_id, name=f"{target.document.name} copy")
            self.add_annotation(document)
            self.objects[new_id].mark_unsaved()
        return new_id

    def remove(self, object_id: str) -> None:
        self.get(object_id)
        del self.objects[object_id]
        self.paths.pop(object_id, None)
        self._saved_signatures.pop(object_id, None)
