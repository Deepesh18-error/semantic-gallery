import unittest

from audio_benchmark import joined_transcript, word_error_rate


class AudioBenchmarkTests(unittest.TestCase):
    def test_word_error_rate_and_silence(self):
        self.assertEqual(word_error_rate("AWS VPC private subnet", "AWS VPC private subnet"), 0)
        self.assertEqual(word_error_rate("", ""), 0)
        self.assertEqual(word_error_rate("", "invented speech"), 1)
        self.assertEqual(word_error_rate("AWS VPC", "AWS VPN"), .5)

    def test_passage_overlap_does_not_inflate_error_rate(self):
        parts = [{"text": "AWS VPC private subnet"},
                 {"text": "private subnet route table"}]
        self.assertEqual(joined_transcript(parts), "aws vpc private subnet route table")


if __name__ == "__main__":
    unittest.main()
