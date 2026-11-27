import Foundation
import FoundationModels

@main struct OnDevice {
    static func main() async {
        guard SystemLanguageModel.default.availability == .available else {
            FileHandle.standardError.write(Data("On-device model unavailable\n".utf8))
            exit(2)
        }
        if CommandLine.arguments.contains("--check") { print("ready"); return }
        let bytes = FileHandle.standardInput.readDataToEndOfFile()
        guard let job = try? JSONDecoder().decode(Job.self, from: bytes) else { exit(3) }
        do {
            let session = LanguageModelSession(instructions: job.instructions)
            let response = try await session.respond(to: job.input, options: GenerationOptions(sampling: .greedy))
            print(response.content)
        } catch {
            FileHandle.standardError.write(Data("Model request unavailable: \(error)\n".utf8))
            exit(4)
        }
    }
}
struct Job: Decodable {
    let instructions: String
    let input: String
}
