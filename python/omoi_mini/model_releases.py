"""Vertrauenswürdige Modellkandidaten, bewusst ohne Downloadfreigabe."""
from dataclasses import dataclass

from .model_installer import Manifest, ModelFile


@dataclass(frozen=True)
class ModelRelease:
    repository: str
    license_label: str
    manifest: Manifest
    approved: bool = False


# HF API blobs=true und gepinnte kleine Dateien am 24.11.2026 geprüft.
# LFS-SHA256 des Gewichts stammt ausschließlich aus Upstream-Metadaten.
# Lizenzlabel ist keine abschließende Rechte-/Runtime-Freigabe.
RELEASES = (
    ModelRelease("Systran/faster-whisper-small", "MIT", Manifest(
        "whisper-small-cuda", "536b0662742c02347bc0e980a01041f333bce120", (
            ModelFile("config.json", 2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
            ModelFile("model.bin", 483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
            ModelFile("tokenizer.json", 2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
            ModelFile("vocabulary.txt", 459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
            ModelFile("README.md", 1998, "329373481008c7c38654aff8ecdcf0163c211557cc7ba8e2ef6f2f84b4f75ec8"),
        ))),
    ModelRelease('google/gemma-4-E2B-it-qat-q4_0-gguf', "Apache-2.0", Manifest(
        'gemma4-e2b-gguf', '675cff42a74c774d6cb76f76d8eacb49b48c9b93', (
            ModelFile('README.md', 29260, '942e93b96c2f7f2b5fdd8d5578f0f1215e4a354f12e202999b4fbc864bb02ef5'),
            ModelFile('gemma-4-E2B_q4_0-it.gguf', 3349516256, 'fa401b55b07ee70a54c6dae3903c783a6e65064312529ea57175cb5f8dec6634'),
        ))),
    ModelRelease('Qwen/Qwen3-TTS-12Hz-1.7B-Base', "Apache-2.0", Manifest(
        'qwen3-tts-1.7b', 'fd4b254389122332181a7c3db7f27e918eec64e3', (
            ModelFile('README.md', 57817, '621b7e88f1867bc03d817176fc1f7f55f4b5a70654b2a07fdcda1e169efb024b'),
            ModelFile('config.json', 4494, 'b4f01752d15a488abde3e1ab44723ae4f4b9e68a4037257b098b3737893cc1f9'),
            ModelFile('generation_config.json', 245, 'f1b90b4513f3b34c62851049e2492d7b4c5940daf1276f89c82b8ef04127f3aa'),
            ModelFile('merges.txt', 1671839, '599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3'),
            ModelFile('model.safetensors', 3857413744, '38fc7fc51c5e776e840414b6fd443962e9411b9654888fd7913e4da643cb857c'),
            ModelFile('preprocessor_config.json', 127, 'efdde1022ea9d76928bf7a9cd53139138f5ba2e466e837f08f6105ab1af1c119'),
            ModelFile('speech_tokenizer/config.json', 2336, 'ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167'),
            ModelFile('speech_tokenizer/configuration.json', 76, '6bc26d64eb5024b4d1dab5a52371958b429256d6c9d59787f1f5294a54e0cebd'),
            ModelFile('speech_tokenizer/model.safetensors', 682293092, '836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258'),
            ModelFile('speech_tokenizer/preprocessor_config.json', 234, 'fcb3805e597e786d4067706e602f6688524640f8d3396790e2e09b5942fcbdfb'),
            ModelFile('tokenizer_config.json', 7344, 'dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670'),
            ModelFile('vocab.json', 2776833, 'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910'),
        ))),
    ModelRelease('Qwen/Qwen3-TTS-12Hz-0.6B-Base', "Apache-2.0", Manifest(
        'qwen3-tts-0.6b', '5d83992436eae1d760afd27aff78a71d676296fc', (
            ModelFile('README.md', 3640, '181187b6057906bd960bc7f938d0b7a16652509776a0d52c4885b4ae5ccda0ea'),
            ModelFile('config.json', 4494, '2e714c787c8edb98b05432685cddb634add2de4d4e645f653d68251ef72ba011'),
            ModelFile('generation_config.json', 245, 'f1b90b4513f3b34c62851049e2492d7b4c5940daf1276f89c82b8ef04127f3aa'),
            ModelFile('merges.txt', 1671839, '599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3'),
            ModelFile('model.safetensors', 1829344272, '180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6'),
            ModelFile('preprocessor_config.json', 127, 'efdde1022ea9d76928bf7a9cd53139138f5ba2e466e837f08f6105ab1af1c119'),
            ModelFile('speech_tokenizer/config.json', 2336, 'ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167'),
            ModelFile('speech_tokenizer/configuration.json', 76, '6bc26d64eb5024b4d1dab5a52371958b429256d6c9d59787f1f5294a54e0cebd'),
            ModelFile('speech_tokenizer/model.safetensors', 682293092, '836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258'),
            ModelFile('speech_tokenizer/preprocessor_config.json', 234, 'fcb3805e597e786d4067706e602f6688524640f8d3396790e2e09b5942fcbdfb'),
            ModelFile('tokenizer_config.json', 7344, 'dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670'),
            ModelFile('vocab.json', 2776833, 'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910'),
        ))),
    ModelRelease('OpenVINO/whisper-medium-int8-ov', "Apache-2.0", Manifest(
        'whisper-medium-ov', '8d43cce846729381f56bd45a1c70925cee2222ff', (
            ModelFile('README.md', 4763, 'ccbd41012f7cf1e9200227104bdd09511e7370802db2bd2801f74ad821e94ddc'),
            ModelFile('added_tokens.json', 34604, '9715fd2243b6f06a5858b5e32950d2853f73dd5bc201aafcf76f5082a2d8acd1'),
            ModelFile('config.json', 1326, '468a4c8675c49b8ab27717fe02223b6ae2565dc2b6411d017e7e04aec8497dec'),
            ModelFile('generation_config.json', 3750, 'd5dd0ffb1125f44ecae80a93f04c06c8127c2a2aff4ea2feec171d2fda919612'),
            ModelFile('merges.txt', 493869, '2df2990a395e35e8dfbc7511e08c12d56018d8d04691e0133e5d63b21e154dc6'),
            ModelFile('normalizer.json', 52666, 'bf1c507dc8724ca9cf9903640dacfb69dae2f00edee4f21ceba106a7392f26dd'),
            ModelFile('openvino_config.json', 447, 'fab0fd49ebf95e151c28007a44c0ca1dad31576e02b2e054e1c5767994bae19f'),
            ModelFile('openvino_decoder_model.bin', 459016627, '4ff8ba47640c78e30fdfd6b1c91f69ead9f67047877ee38baf83349927b429eb'),
            ModelFile('openvino_decoder_model.xml', 2160825, 'ddc857598959ed2b6f290172da1478cfa29249f1b5b8080ed91e8048e266f7fa'),
            ModelFile('openvino_detokenizer.bin', 736181, '2542d1fe6c4c5d838e7b7c61b24996b94ae0cb54e64ff6dbaab2b9dd5ddd7ca0'),
            ModelFile('openvino_detokenizer.xml', 9699, '9adb820ef4576d3c317bf897afa04255f1fe1f87f63650e60143894e6ff6f2ba'),
            ModelFile('openvino_encoder_model.bin', 313391216, 'e74ff119334068b54a6da0b039d9f8a7a8caa5a2b0a5e940f11eb4a20b2251e1'),
            ModelFile('openvino_encoder_model.xml', 1142319, 'fbd9387fee6a1da4ebb779227aeb77f4520370f388c0af8e215f0ad1a7cb2a44'),
            ModelFile('openvino_tokenizer.bin', 1898933, '846f3c65f7a71f120fce7aaaf41b342f0767eb13e3c4e7c2a54f1d49b5c38fda'),
            ModelFile('openvino_tokenizer.xml', 27011, 'f15e4c9573899623a9dee76f43ec204901d7bb2721f1e21531b3e82c63fc6362'),
            ModelFile('preprocessor_config.json', 356, '994838f1fa6462c8b9b3c90edada831f11f3dd8b4664634e18f4694d005c9dbf'),
            ModelFile('special_tokens_map.json', 2194, 'e67ae3a0aaa99abcd9f187138e12db1f65c16a14761c50ef10eef2c174a7a691'),
            ModelFile('tokenizer.json', 3930494, '7b469ff15eb7816315aa45eec391f5943d639b9d73d110f5c003df5192fd54e3'),
            ModelFile('tokenizer_config.json', 282713, '21a4fc0483c14b87f4e0bbc177a9a357479bfa7c95aaf21ad53d71e9c5afafb9'),
            ModelFile('vocab.json', 835550, '8f680bba319e01a653d2e8a5dbc17a9157179e0576e6ce74ce0c06356c6e24f9'),
        ))),
)


def release_for(model_id: str) -> ModelRelease | None:
    return next((r for r in RELEASES if r.manifest.model_id == model_id), None)
